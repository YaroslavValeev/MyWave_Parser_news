from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import aiosqlite
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config.settings import config
from core.scheduler import (
    ParseAllSourcesBusyError,
    ParseAllSummary,
    parse_all_sources,
    parse_all_sources_busy,
)
from nlp.openai_client import get_openai_client
from services.channel_engagement import run_channel_engagement
from services.manual_collect import collect_single_source, guess_source_type
from storage.data import count_channel_commenters
from services.media_upload import find_local_cover_paths, media_upload_url, upload_cover_image
from services.publication import PublicationSendError, PublicationService
from services.raw_feed_sync import sync_media_fields
from storage.repository import AsyncNewsRepository
from telegram_bot.client_copy import training_media_request_html
from telegram_bot.access import is_bot_operator
from telegram_bot.views import (
    REVIEW_QUEUE_LIMIT,
    format_report,
    format_review_queue_summary,
    format_stats,
    handle_author_rewrite,
    handle_callback,
    review_queue_keyboard,
    save_owner_review_comment,
    show_review_item,
    show_review_item_card,
)
from utils.media_utils import media_path_to_public_url, media_path_to_raw_feed_url, normalize_media_ref
from utils.item_freshness import review_max_age_days

from .keyboards import (
    AuthorDecisionAction,
    MENU_HELP,
    MENU_PARSE,
    MENU_PROBE,
    MENU_PUBLISH,
    MENU_REVIEW,
    MENU_STATS,
    PersonalReviewAction,
    ReviewAction,
    main_menu_reply_keyboard,
)
from .middlewares import RoleMiddleware

LOGGER = logging.getLogger(__name__)

_requeue_last_by_user: dict[int, float] = {}
_requeue_calls_hour: list[tuple[float, int]] = []


def _requeue_rate_check(user_id: int) -> tuple[bool, str]:
    """Лимит частоты /requeue_nlp (защита OpenAI и БД)."""
    now = time.time()
    cd = max(1, int(getattr(config, "REQUEUE_NLP_COOLDOWN_SECONDS", 60)))
    mx = max(1, int(getattr(config, "REQUEUE_NLP_MAX_PER_HOUR", 20)))
    last = _requeue_last_by_user.get(user_id, 0.0)
    if now - last < cd:
        wait_s = int(cd - (now - last)) + 1
        return False, f"Подождите {wait_s} с перед следующей командой /requeue_nlp."
    global _requeue_calls_hour
    _requeue_calls_hour = [(t, u) for t, u in _requeue_calls_hour if now - t < 3600]
    used = sum(1 for _t, u in _requeue_calls_hour if u == user_id)
    if used >= mx:
        return False, f"Лимит /requeue_nlp: не более {mx} раз в час (настройка REQUEUE_NLP_MAX_PER_HOUR)."
    return True, ""


def _format_elapsed_minutes(mins: int) -> str:
    """Человекочитаемо: до 59 — только минуты, иначе часы и минуты."""
    mins = max(1, int(mins))
    if mins < 60:
        return f"{mins} мин"
    h, m = divmod(mins, 60)
    if m == 0:
        return f"{h} ч"
    return f"{h} ч {m} мин"


def _mask_token(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return "не задан"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]} (len={len(token)})"


def _is_local_site_base(site_base: str) -> bool:
    value = (site_base or "").strip().lower()
    if not value:
        return False
    return any(
        marker in value
        for marker in ("127.0.0.1", "localhost", "0.0.0.0")
    )


def _upload_endpoint_reachable(status_code: int | None) -> bool:
    """POST-only upload routes often answer GET with 405 — это нормально."""
    if status_code is None:
        return False
    if 200 <= status_code < 300:
        return True
    return status_code in {405, 401, 403}


async def _build_media_diag_report(repo: AsyncNewsRepository) -> str:
    endpoint = media_upload_url()
    upload_url_raw = str(getattr(config, "MEDIA_UPLOAD_URL", "") or "").strip()
    upload_endpoint_raw = str(getattr(config, "MEDIA_UPLOAD_ENDPOINT", "") or "").strip()
    token = str(getattr(config, "MEDIA_UPLOAD_TOKEN", "") or "").strip()
    site_base = str(getattr(config, "SITE_BASE_URL", "") or "").strip()
    endpoint_status: int | None = None
    endpoint_error: str | None = None
    posts_status: int | None = None
    posts_error: str | None = None
    fallback_n: int | None = None
    local_probe_info: str | None = None

    if endpoint:
        try:
            response = await asyncio.to_thread(requests.get, endpoint, timeout=8)
            endpoint_status = int(response.status_code)
        except requests.RequestException as exc:
            endpoint_error = type(exc).__name__

    try:
        posts_url = (site_base.rstrip("/") + "/api/blog/posts") if site_base else ""
        if posts_url:
            response = await asyncio.to_thread(
                requests.get,
                posts_url,
                timeout=15,
            )
            if response.status_code == 200:
                posts_status = 200
                payload = response.json()
                items = payload if isinstance(payload, list) else payload.get("items", [])
                fallback_n = 0
                for item in items[:20]:
                    cover = str(item.get("cover_image_url") or item.get("image_url") or "")
                    if "place1logo" in cover.lower() or "fallback" in cover.lower():
                        fallback_n += 1
            else:
                posts_status = int(response.status_code)
    except (requests.RequestException, ValueError) as exc:
        posts_error = type(exc).__name__

    try:
        queue = await repo.list_review_queue(limit=20)
        first_local_path = None
        for item in queue:
            paths = find_local_cover_paths(item)
            if paths:
                first_local_path = paths[0]
                break
        if first_local_path:
            public_url = media_path_to_public_url(str(first_local_path))
            if public_url and site_base:
                resolved = site_base.rstrip("/") + public_url
                try:
                    image_response = await asyncio.to_thread(requests.get, resolved, timeout=12)
                    local_probe_info = (
                        f"<code>{html.escape(resolved)}</code> → <code>{image_response.status_code}</code>"
                    )
                except requests.RequestException as exc:
                    local_probe_info = (
                        f"<code>{html.escape(resolved)}</code> → ошибка "
                        f"<code>{html.escape(type(exc).__name__)}</code>"
                    )
    except (ValueError, TypeError):
        LOGGER.exception("media diag local path probe failed")

    token_ok = bool(token)
    localhost_site = _is_local_site_base(site_base)
    endpoint_ok = _upload_endpoint_reachable(endpoint_status)
    posts_ok = posts_status == 200
    fallback_ok = fallback_n is None or fallback_n == 0

    if localhost_site:
        verdict = "❌ SITE_BASE_URL указывает на localhost — на VPS так нельзя."
    elif not endpoint:
        verdict = "⚠️ Endpoint загрузки не настроен."
    elif endpoint_ok and posts_ok and fallback_ok:
        verdict = "✅ Цепочка обложек выглядит исправной."
    elif endpoint_status in (401, 403):
        verdict = "❌ Сайт отвергает доступ к upload endpoint (токен/права)."
    elif posts_ok and not fallback_ok:
        verdict = "⚠️ Upload работает, но на витрине есть fallback-обложки."
    else:
        verdict = "⚠️ Обнаружены проблемы в цепочке обложек."

    lines = [
        "<b>🧪 Media diagnostics</b>",
        "",
        f"<b>Итог:</b> {verdict}",
        "",
        "<b>Коротко по статусам:</b>",
        f"• Токен upload: {'✅ задан' if token_ok else '❌ не задан'}",
        (
            f"• Upload endpoint: ✅ {endpoint_status}"
            + (" (GET→405, POST-маршрут жив)" if endpoint_status == 405 else "")
            if endpoint_ok
            else (
                f"• Upload endpoint: ❌ {endpoint_status}"
                if endpoint_status is not None
                else (
                    f"• Upload endpoint: ❌ ошибка {html.escape(endpoint_error)}"
                    if endpoint_error
                    else "• Upload endpoint: ❌ не настроен"
                )
            )
        ),
        (
            f"• /api/blog/posts: ✅ {posts_status}"
            if posts_ok
            else (
                f"• /api/blog/posts: ❌ {posts_status}"
                if posts_status is not None
                else (
                    f"• /api/blog/posts: ❌ ошибка {html.escape(posts_error)}"
                    if posts_error
                    else "• /api/blog/posts: ⚠️ не проверен"
                )
            )
        ),
    ]

    if fallback_n is not None:
        lines.append(
            f"• fallback-обложки (top20): {'✅ 0' if fallback_n == 0 else f'⚠️ {fallback_n}'}"
        )
    if local_probe_info:
        lines.append(f"• Локальная статика: {local_probe_info}")

    if localhost_site:
        lines.append(
            "• SITE_BASE_URL: ❌ localhost — задайте <code>https://mywavewake.ru</code> в .env на сервере"
        )

    lines.extend(
        [
            "",
            "<b>Что делать:</b>",
            "1) На VPS в <code>/opt/bot3/parser-new-bot/.env</code>: "
            "<code>SITE_BASE_URL=https://mywavewake.ru</code>, "
            "<code>MEDIA_UPLOAD_ENDPOINT=/api/blog/media/upload</code>.",
            "2) Если Upload endpoint = 401/403, проверьте MEDIA_UPLOAD_TOKEN и права API на сайте.",
            "3) Если /api/blog/posts не отвечает, проверьте SITE_BASE_URL и доступность сайта.",
            "4) fallback-обложки: перезалить обложки для старых постов после исправления SITE_BASE_URL.",
            "",
            "<b>Тех. детали:</b>",
            f"• SITE_BASE_URL: <code>{html.escape(site_base or 'не задан')}</code>",
            f"• MEDIA_UPLOAD_URL(raw): <code>{html.escape(upload_url_raw or 'не задан')}</code>",
            f"• MEDIA_UPLOAD_ENDPOINT(raw): <code>{html.escape(upload_endpoint_raw or 'не задан')}</code>",
            f"• UPLOAD_ENDPOINT(effective): <code>{html.escape(endpoint or 'не настроен')}</code>",
            f"• TOKEN(masked): <code>{html.escape(_mask_token(token))}</code>",
            "",
            "<i>Если endpoint=401/403 — проблема в токене/правах API сайта, а не в Telegram-боте.</i>",
        ]
    )
    return "\n".join(lines)


class ProbeForm(StatesGroup):
    """Ожидание URL после кнопки «Проверить источник»."""

    waiting_url = State()


class ReviewCommentForm(StatesGroup):
    """Ожидание текста после кнопки «Комментарий» на карточке ревью."""

    waiting_text = State()


class ReviewVoiceForm(StatesGroup):
    """Ожидание голосового после кнопки «Голосовой комментарий»."""

    waiting_voice = State()


class ReviewCoverForm(StatesGroup):
    """Ожидание изображения обложки для карточки ревью."""

    waiting_media = State()


class ReviewPublishScheduleForm(StatesGroup):
    """Ожидание даты/времени публикации."""

    waiting_datetime = State()


def _parse_schedule_local_to_utc(raw: str) -> tuple[datetime | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, "Пустой ввод."
    lower = text.lower()
    if lower in {"сейчас", "now"}:
        return datetime.now(timezone.utc), None
    local_tz = ZoneInfo(config.SCHEDULER_TIMEZONE)
    now_local = datetime.now(local_tz)
    parsed: datetime | None = None
    with_year = False
    for fmt, has_year in (("%d.%m.%Y %H:%M", True), ("%d.%m %H:%M", False)):
        try:
            parsed = datetime.strptime(text, fmt)
            with_year = has_year
            break
        except ValueError:
            continue
    if parsed is None:
        return None, "Неверный формат. Используйте ДД.ММ.ГГГГ ЧЧ:ММ или ДД.ММ ЧЧ:ММ."
    if not with_year:
        parsed = parsed.replace(year=now_local.year)
    local_dt = parsed.replace(tzinfo=local_tz)
    if not with_year and local_dt < now_local:
        local_dt = local_dt.replace(year=local_dt.year + 1)
    return local_dt.astimezone(timezone.utc), None


HELP_HTML = (
    "<b>Команды MyWave Parser</b>\n\n"
    "📥 <b>Собрать</b> — полный проход по <b>всем</b> источникам из конфига. "
    "Может занять <b>несколько минут</b>; второе сообщение «Сбор завершён» придёт, когда цикл закончится.\n"
    "🔎 <b>Проверить источник</b> — одна ссылка: тип (tg / youtube / rss / website) по URL и пробный сбор "
    "(до 5 записей).\n"
    f"📋 <b>Ревью</b> — список до {REVIEW_QUEUE_LIMIT} материалов (<code>review</code>, затем <code>new</code>); "
    "откройте материал кнопкой «Открыть». <b>Inline-кнопки</b> (одобрить, комментарий текстом/голосом, источник, NLP, "
    "обложка, очередь публикации, переписать саммари) — <b>под сообщением карточки</b>, не в нижнем меню. "
    "Нижние шесть кнопок — общие команды бота. После решения по карточке бот предложит следующую в очереди.\n"
    "После <b>комментария владельца</b>, если есть <b>локальная</b> обложка и в .env заданы "
    "<code>SITE_BASE_URL</code> + <code>MEDIA_UPLOAD_TOKEN</code> (+ endpoint загрузки), бот автоматически пробует отдать "
    "её сайту и обновить <code>raw_feed.cover_image_url</code> без отдельной кнопки.\n"
    "Команда <code>/item ID</code> — открыть <b>любой</b> материал по id без смены статуса: удобно для "
    "повторной замены обложки у уже витринных / опубликованных строк.\n"
    "📤 <b>Опубликовать</b> — очередь в целевой чат (<code>CHANNEL_ID</code> или <code>TELEGRAM_CHANNEL_ID</code>).\n"
    "📊 <b>Статус</b> — краткая сводка по БД (то же, что <code>/stats</code>).\n"
    "🧪 <b>/media_diag</b> — экспресс-диагностика цепочки обложек (endpoint/token/fallback).\n"
    "Команда <code>/report</code> — развёрнутый отчёт: очередь публикации, канал, подсказки по ошибкам.\n\n"
    "Команды: <code>/parse</code>, <code>/probe</code>, <code>/cancel</code>, <code>/stats</code>, <code>/report</code>, <code>/training_copy</code>.\n"
    "<code>/requeue_nlp [N]</code> — повторно отправить в NLP до N записей из <code>error</code> (по умолчанию 30, максимум 500).\n"
    "Используйте после устранения причины ошибок NLP: например, неверный <code>OPENAI_API_KEY</code>, недоступная модель "
    "или временный сетевой сбой API.\n"
    "Есть лимит частоты: <code>REQUEUE_NLP_COOLDOWN_SECONDS</code>, <code>REQUEUE_NLP_MAX_PER_HOUR</code> (в .env).\n\n"
    "<i>Сбор и публикация: при заданном <code>OWNER_USER_ID</code> — только для этих user id.</i>"
)

_COVER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


def _cover_suffix_from_message(message: Message) -> str:
    document = message.document
    if document and document.file_name:
        suffix = Path(document.file_name).suffix.lower()
        if suffix in _COVER_IMAGE_EXTENSIONS:
            return suffix
    mime = str(getattr(document, "mime_type", "") or "").lower()
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    if mime == "image/gif":
        return ".gif"
    return ".jpg"


def _prepend_media_ref(existing: object, new_ref: str) -> str:
    refs = [new_ref]
    if isinstance(existing, str):
        candidates = [part.strip() for part in existing.splitlines() if part.strip()]
    elif isinstance(existing, (list, tuple)):
        candidates = [str(part).strip() for part in existing if str(part).strip()]
    else:
        candidates = []
    for candidate in candidates:
        normalized = normalize_media_ref(candidate, media_type="image")
        if normalized and normalized not in refs:
            refs.append(normalized)
    return "\n".join(refs)


async def _store_review_cover(
    repo: AsyncNewsRepository,
    message: Message,
    item_id: int,
) -> tuple[str, str, str]:
    item = await repo.get_item(item_id)
    if not item:
        raise ValueError("Материал не найден")

    photo = message.photo[-1] if message.photo else None
    document = message.document
    if not photo and not document:
        raise ValueError("Пришлите фото или файл-изображение")
    if document and not str(document.mime_type or "").lower().startswith("image/"):
        raise ValueError("Файл должен быть изображением")

    file_id = photo.file_id if photo else document.file_id  # type: ignore[union-attr]
    tg_file = await message.bot.get_file(file_id)
    out_dir = Path("downloads") / "review_media"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = _cover_suffix_from_message(message)
    path = out_dir / f"item-{item_id}-owner-cover{suffix}"
    await message.bot.download(tg_file, destination=path)

    public_url = media_path_to_public_url(str(path))
    if not public_url:
        raise ValueError("Не удалось получить публичный путь для обложки")
    upload_result = await upload_cover_image(path, item_id=item_id, item=item)
    upload_error = "" if upload_result.ok else upload_result.error
    stored_url = upload_result.url if upload_result.ok else public_url
    raw_feed_url = media_path_to_raw_feed_url(stored_url)

    images = _prepend_media_ref(item.get("images"), stored_url)
    await repo.update_item_media(item_id, images=images, videos=item.get("videos"))
    await repo.log_event(
        item_id,
        "info",
        "owner_cover_uploaded",
        {
            "user_id": message.from_user.id if message.from_user else None,
            "username": message.from_user.username if message.from_user else None,
            "cover_image_url": stored_url,
            "local_cover_url": public_url,
            "raw_feed_cover_image_url": raw_feed_url,
            "upload_error": upload_error,
        },
    )
    item_after = await repo.get_item(item_id)
    sheet_synced = bool(item_after and await sync_media_fields(item_after))
    if raw_feed_url and not sheet_synced:
        upload_error = upload_error or "raw_feed_sync_failed"
        raw_feed_url = ""
    return stored_url, raw_feed_url, upload_error


async def _run_with_typing(bot: Bot, chat_id: int, coro):
    """Пока выполняется coro, раз в ~4.5 с шлём chat action «печатает»."""
    stop = asyncio.Event()

    async def typing_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.5)
                return
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)

    task = asyncio.create_task(typing_loop())
    try:
        return await coro
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _format_parse_done(report: ParseAllSummary) -> str:
    if report.sources_total == 0:
        return (
            "✅ Цикл завершён.\n\n"
            "Список источников пуст — проверьте <code>storage.sources</code> / загрузку предустановок."
        )
    return (
        "✅ <b>Сбор завершён</b>\n\n"
        f"⏱ Время: <b>{report.elapsed_seconds:.1f}</b> с\n"
        f"📰 Новых новостей в БД: <b>{report.news_saved}</b>\n"
        f"📇 Контактов сохранено: <b>{report.contacts_saved}</b>\n"
        f"📡 Источники: успешно <b>{report.sources_ok}</b> из <b>{report.sources_total}</b>"
        f" (ошибок: <b>{report.sources_failed}</b>)\n\n"
        "<i>Между стартом и этим сообщением бот мог слать короткие напоминания «ещё идёт» — "
        "итог со статистикой всегда одним финальным сообщением.</i>"
    )


async def _run_probe(message: Message, bot: Bot, url: str) -> None:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        await message.answer("Нужна ссылка, начинающаяся с <code>http://</code> или <code>https://</code>.", parse_mode="HTML")
        return
    guessed = guess_source_type(url)
    safe = html.escape(url)
    await message.answer(
        f"Тип по URL: <b>{html.escape(guessed)}</b> (эвристика).\n"
        f"<code>{safe}</code>\n\n"
        "Пробный сбор (до <b>5</b> записей)…",
        parse_mode="HTML",
    )
    try:
        result = await _run_with_typing(
            bot,
            message.chat.id,
            collect_single_source(url, source_type=None, limit=5),
        )
        await message.answer(
            "✅ <b>Проверка завершена</b>\n\n"
            f"Тип: <code>{html.escape(result.source_type)}</code>\n"
            f"Получено записей: <b>{result.total}</b>\n"
            f"Сохранено новых в БД: <b>{result.saved}</b>\n"
            f"Отфильтровано по дате: <b>{result.filtered_out}</b>\n"
            f"Контактов: <b>{result.contacts_saved}</b>",
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("probe failed url=%s", url)
        await message.answer(
            "❌ Не удалось собрать источник.\n"
            f"<pre>{html.escape(str(exc)[:500])}</pre>\n\n"
            "Для Telegram часто нужны Telethon-сессия и доступ к каналу.",
            parse_mode="HTML",
        )


def create_router(repository: AsyncNewsRepository, bot: Bot) -> Router:
    """Роутер команд, FSM проверки источника, callback ревью."""
    router = Router()
    router.message.middleware(RoleMiddleware())
    router.callback_query.middleware(RoleMiddleware())
    repo = repository

    @router.message(Command("start"))
    async def cmd_start(message: Message):
        await message.answer(
            "MyWave Parser — бот редакции.\n"
            "<i>Ревью материала:</i> нажмите «📋 Ревью», затем «Открыть» у строки — "
            "<b>inline-кнопки</b> появятся под карточкой новости.\n\n"
            "Выберите действие в меню ниже или команду в «Menu».",
            parse_mode="HTML",
            reply_markup=main_menu_reply_keyboard(),
        )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        if await state.get_state() is None:
            return
        await state.clear()
        await message.answer("Сценарий отменён.")

    @router.message(or_f(Command("help"), F.text == MENU_HELP))
    async def cmd_help(message: Message):
        await message.answer(HELP_HTML, parse_mode="HTML")

    @router.message(Command("training_copy"))
    async def cmd_training_copy(message: Message):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Команда только для оператора бота.")
            return
        await message.answer(training_media_request_html(), parse_mode="HTML")

    async def _do_parse(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer(
                "Сбор доступен только владельцу. Задайте свой user id в .env: <code>OWNER_USER_ID</code>.",
                parse_mode="HTML",
            )
            return
        if parse_all_sources_busy():
            await message.answer(
                "⏳ Уже выполняется <b>полный</b> сбор (по расписанию или другая команда). "
                "Дождитесь сообщения «Сбор завершён» — параллельный второй обход отключён.",
                parse_mode="HTML",
            )
            return

        await message.answer(
            "⏳ Запускаю сбор по <b>всем</b> источникам…\n"
            "Это может занять несколько минут. "
            "Следующее сообщение придёт, когда цикл <b>полностью</b> завершится.",
            parse_mode="HTML",
        )

        stop_hb = asyncio.Event()
        t0 = time.monotonic()

        async def _parse_heartbeat() -> None:
            """Через 2 мин — первое напоминание, далее каждые 3 мин, пока идёт сбор."""
            first_wait = True
            while not stop_hb.is_set():
                sec = 120 if first_wait else 180
                first_wait = False
                try:
                    await asyncio.wait_for(stop_hb.wait(), timeout=sec)
                    return
                except asyncio.TimeoutError:
                    pass
                mins = max(1, int((time.monotonic() - t0) // 60))
                elapsed = _format_elapsed_minutes(mins)
                if mins <= 3:
                    body = (
                        "⏳ Сбор всё ещё идёт (много источников или медленные сети). "
                        "Итог придёт <b>одним</b> сообщением «Сбор завершён», как только обход закончится."
                    )
                else:
                    extra = ""
                    if mins >= 90:
                        extra = (
                            "\n\nЕсли ожидали <b>несколько минут</b>, а прошли часы — "
                            "остановите процесс (Ctrl+C) и в .env задайте "
                            "<code>TELEGRAM_SKIP_MEDIA_FULL_COLLECT=true</code>, затем перезапуск "
                            "(обложки Telegram будут пустыми, но t.me не пойдёт как картинка)."
                        )
                    body = (
                        f"⏳ Сбор продолжается уже около <b>{elapsed}</b>.\n"
                        "Часто долго из‑за <b>Telegram</b> (скачивание медиа по каждому посту). "
                        "Ускорить: <code>TELEGRAM_SKIP_MEDIA_FULL_COLLECT=true</code> "
                        "(обложки Telegram будут пустыми) или уменьшить "
                        "<code>TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS</code> (по умолчанию 90). "
                        "Итог — сообщением «Сбор завершён»."
                        + extra
                    )
                with contextlib.suppress(Exception):
                    await message.answer(body, parse_mode="HTML")

        hb = asyncio.create_task(_parse_heartbeat())
        try:
            report = await _run_with_typing(bot, message.chat.id, parse_all_sources())
            await message.answer(_format_parse_done(report), parse_mode="HTML")
        except ParseAllSourcesBusyError:
            await message.answer(
                "⏳ Уже выполняется <b>полный</b> сбор (по расписанию или предыдущая команда). "
                "Дождитесь одного сообщения «Сбор завершён» — второй параллельный обход отключён, "
                "чтобы не перегружать сеть и Telegram.",
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("parse_all_sources failed from bot")
            await message.answer("❌ Ошибка сбора. Подробности в логе сервера.")
        finally:
            stop_hb.set()
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb

    @router.message(or_f(Command("parse"), F.text == MENU_PARSE))
    async def cmd_parse(message: Message):
        await _do_parse(message)

    @router.message(F.text == MENU_PROBE)
    async def probe_button(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer(
                "Доступно владельцу (<code>OWNER_USER_ID</code>).",
                parse_mode="HTML",
            )
            return
        await state.set_state(ProbeForm.waiting_url)
        await message.answer(
            "Вставьте <b>одной строкой</b> URL (RSS, YouTube, t.me/… или сайт).\n"
            "Или сразу: <code>/probe https://…</code>\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )

    @router.message(ProbeForm.waiting_url, F.text, ~F.text.startswith("/"))
    async def probe_got_url(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        text = (message.text or "").strip()
        if text.lower() in {"/cancel", "отмена"}:
            await state.clear()
            await message.answer("Отменено.")
            return
        await state.clear()
        await _run_probe(message, bot, text)

    @router.message(Command("probe"))
    async def cmd_probe(message: Message, command: CommandObject, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Доступно владельцу (<code>OWNER_USER_ID</code>).", parse_mode="HTML")
            return
        await state.clear()
        args = (command.args or "").strip()
        if not args:
            await message.answer(
                "Пример: <code>/probe https://www.youtube.com/channel/…</code>\n"
                "или кнопка «Проверить источник».",
                parse_mode="HTML",
            )
            return
        await _run_probe(message, bot, args)

    async def _do_review(message: Message) -> None:
        items = await repo.list_review_queue(limit=REVIEW_QUEUE_LIMIT)
        if not items:
            extra = ""
            counts = await repo.get_status_counts()
            n_err = int(counts.get("error", 0))
            if n_err > 0:
                extra = (
                    f"\n\nВ базе ещё <b>{n_err}</b> записей со статусом <code>error</code> — "
                    "они сюда не попадают: сначала разберите ошибку или переведите запись в "
                    "<code>new</code> (например, после правки в БД / пайплайне)."
                )
            await message.answer(
                "Нет свежих материалов для ревью "
                f"(показываем только за последние {review_max_age_days()} дн.; "
                "статусы <code>review</code> или <code>new</code>)."
                + extra,
                parse_mode="HTML",
            )
            return
        if len(items) == 1:
            await show_review_item(repo, items[0]["id"], message)
            return
        text = format_review_queue_summary(items)
        kb = review_queue_keyboard(items)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @router.message(or_f(Command("review"), F.text == MENU_REVIEW))
    async def cmd_review(message: Message):
        await _do_review(message)

    @router.message(Command("item"))
    async def cmd_item(message: Message, command: CommandObject):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Доступно владельцу (<code>OWNER_USER_ID</code>).", parse_mode="HTML")
            return
        raw = (command.args or "").strip()
        if not raw or not raw.isdigit():
            await message.answer(
                "Пример: <code>/item 1368</code>\n"
                "Команда открывает материал по id без смены статуса, чтобы можно было заменить обложку "
                "или перепроверить карточку.",
                parse_mode="HTML",
            )
            return
        item_id = int(raw)
        item = await repo.get_item(item_id)
        if not item:
            await message.answer(f"Материал <code>#{item_id}</code> не найден.", parse_mode="HTML")
            return
        banner = (
            f"<b>Открыт материал</b> <code>#{item_id}</code> "
            f"(<code>{html.escape(str(item.get('status') or ''))}</code>) без смены статуса.\n\n"
        )
        await show_review_item_card(repo, item_id, message, banner=banner)

    async def _do_publish(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer(
                "Публикация только для владельца (<code>OWNER_USER_ID</code>).",
                parse_mode="HTML",
            )
            return
        if not config.CHANNEL_ID:
            await message.answer(
                "Не задан <code>CHANNEL_ID</code> (или <code>TELEGRAM_CHANNEL_ID</code>) в .env — некуда публиковать.",
                parse_mode="HTML",
            )
            return
        await message.answer("Проверяю очередь публикации…")
        try:
            svc = PublicationService(repository, bot, config.CHANNEL_ID)
            n = await svc.publish_pending(limit=5)
            await message.answer(f"Отправлено в целевой чат постов: <b>{n}</b>.", parse_mode="HTML")
        except (PublicationSendError, aiosqlite.Error, RuntimeError):
            LOGGER.exception("publish_pending failed from bot")
            await message.answer("Ошибка публикации. Смотрите лог сервера.")

    @router.message(or_f(Command("publish"), F.text == MENU_PUBLISH))
    async def cmd_publish(message: Message):
        await _do_publish(message)

    @router.message(or_f(Command("stats"), F.text == MENU_STATS))
    async def cmd_stats(message: Message):
        try:
            counts = await repo.get_status_counts()
            metrics = await repo.get_processing_summary()
            commenters_n = await count_channel_commenters()
            text = format_stats(counts, metrics, channel_commenters=commenters_n)
            await message.answer(text, parse_mode="HTML")
        except aiosqlite.Error:
            LOGGER.exception("stats failed from bot")
            await message.answer("Не удалось прочитать статистику из БД.")

    @router.message(Command("collect_commenters"))
    async def cmd_collect_commenters(message: Message, command: CommandObject):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Команда только для оператора бота.", parse_mode="HTML")
            return
        channel = (command.args or "").strip()
        await message.answer("Собираю комментаторы каналов…")
        try:
            result = await run_channel_engagement(
                channel_url=channel or None,
                sync_sheet=True,
            )
            st = result.stats
            note = f"\nПримечание: {html.escape(result.note)}" if result.note else ""
            await message.answer(
                "<b>Комментаторы</b>\n"
                f"Сохранено в БД: <b>{result.saved_db}</b>\n"
                f"Sheets: обновлено {result.sheet_updated}, добавлено {result.sheet_appended}\n"
                f"Комментариев собрано: {st.comments_collected if st else 0}\n"
                f"Каналов без discussion: {st.skipped_no_discussion if st else 0}\n"
                f"Ошибок: {result.errors}{note}",
                parse_mode="HTML",
            )
        except Exception:
            LOGGER.exception("collect_commenters failed")
            await message.answer("Ошибка сбора комментаторов. Смотрите лог сервера.")

    @router.message(Command("media_diag"))
    async def cmd_media_diag(message: Message):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Команда только для владельца (<code>OWNER_USER_ID</code>).", parse_mode="HTML")
            return
        try:
            text = await _build_media_diag_report(repo)
            await message.answer(text, parse_mode="HTML")
        except (requests.RequestException, ValueError, aiosqlite.Error):
            LOGGER.exception("media_diag failed")
            await message.answer("Не удалось выполнить media_diag. Смотрите лог сервера.")

    @router.message(Command("requeue_nlp"))
    async def cmd_requeue_nlp(message: Message, command: CommandObject):
        """Повторно поставить в NLP записи со статусом error после устранения причины сбоев."""
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await message.answer("Команда только для владельца (<code>OWNER_USER_ID</code>).", parse_mode="HTML")
            return
        ok_rate, rate_msg = _requeue_rate_check(int(uid))
        if not ok_rate:
            await message.answer(rate_msg)
            return
        raw = (command.args or "").strip()
        limit = 30
        if raw:
            if not raw.isdigit():
                await message.answer(
                    "Укажите число: <code>/requeue_nlp 50</code> (сколько строк error→new, по умолчанию 30, макс. 500).",
                    parse_mode="HTML",
                )
                return
            limit = min(500, max(1, int(raw)))
        try:
            n = await repo.requeue_error_to_new(limit=limit)
        except aiosqlite.Error:
            LOGGER.exception("requeue_nlp failed")
            await message.answer("Ошибка БД. Смотрите лог сервера.")
            return
        _requeue_last_by_user[int(uid)] = time.time()
        _requeue_calls_hour.append((time.time(), int(uid)))
        await message.answer(
            f"В очередь NLP возвращено записей: <b>{n}</b> (error → new).\n"
            f"Следующие шаги: убедитесь, что <code>OPENAI_API_KEY</code> и доступ к модели в порядке; "
            f"через 1–2 минуты нажмите «Статус» или «Ревью».",
            parse_mode="HTML",
        )

    @router.message(Command("report"))
    async def cmd_report(message: Message):
        try:
            counts = await repo.get_status_counts()
            metrics = await repo.get_processing_summary()
            pub_n = await repo.count_publication_queue()
            ch_ok = bool(config.CHANNEL_ID)
            text = format_report(
                counts,
                metrics,
                publication_pending=pub_n,
                channel_configured=ch_ok,
            )
            await message.answer(text, parse_mode="HTML")
        except aiosqlite.Error:
            LOGGER.exception("report failed from bot")
            await message.answer("Не удалось сформировать отчёт из БД.")

    @router.callback_query(ReviewAction.filter())
    async def on_review_callback(
        query: CallbackQuery,
        callback_data: ReviewAction,
        state: FSMContext,
    ):
        uid = query.from_user.id if query.from_user else None
        if not is_bot_operator(uid):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if callback_data.action == "comment":
            await state.set_state(ReviewCommentForm.waiting_text)
            await state.update_data(review_item_id=callback_data.item_id)
            await query.answer()
            if query.message:
                await query.message.answer(
                    "Введите комментарий или экспертное мнение <b>одним сообщением</b>.\n"
                    "<code>/cancel</code> — отмена.",
                    parse_mode="HTML",
                )
            return
        if callback_data.action == "add_cover":
            await state.set_state(ReviewCoverForm.waiting_media)
            await state.update_data(review_item_id=callback_data.item_id)
            await query.answer()
            if query.message:
                await query.message.answer(
                    "Пришлите <b>одно изображение</b> для обложки: фото или файл "
                    "(jpg/png/webp/gif).\n"
                    "Оно станет первым медиа финального Telegram-поста.\n"
                    "Если настроен upload endpoint сайта, бот загрузит изображение туда и запишет "
                    "вернувшийся URL в <code>raw_feed.cover_image_url</code>.\n"
                    "<code>/cancel</code> — отмена.",
                    parse_mode="HTML",
                )
            return
        if callback_data.action == "publish_now":
            await state.set_state(ReviewPublishScheduleForm.waiting_datetime)
            await state.update_data(review_item_id=callback_data.item_id)
            await query.answer()
            if query.message:
                await query.message.answer(
                    "Укажите дату и время публикации.\n"
                    f"Часовой пояс: <b>{html.escape(str(config.SCHEDULER_TIMEZONE))}</b>\n"
                    "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> или <code>ДД.ММ ЧЧ:ММ</code>.\n"
                    "Или отправьте <code>сейчас</code> для немедленной публикации.\n"
                    "<code>/cancel</code> — отмена.",
                    parse_mode="HTML",
                )
            return
        await handle_callback(
            repo,
            query,
            {"action": callback_data.action, "item_id": callback_data.item_id},
        )

    @router.callback_query(PersonalReviewAction.filter())
    async def on_personal_review_callback(
        query: CallbackQuery,
        callback_data: PersonalReviewAction,
        state: FSMContext,
    ):
        uid = query.from_user.id if query.from_user else None
        if not is_bot_operator(uid):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if callback_data.action == "text":
            await state.set_state(ReviewCommentForm.waiting_text)
            await state.update_data(review_item_id=callback_data.item_id)
            await query.answer()
            if query.message:
                await query.message.answer(
                    "Введите комментарий или экспертное мнение <b>одним сообщением</b>.\n"
                    "<code>/cancel</code> — отмена.",
                    parse_mode="HTML",
                )
            return
        if callback_data.action == "voice":
            await state.set_state(ReviewVoiceForm.waiting_voice)
            await state.update_data(review_item_id=callback_data.item_id)
            await query.answer()
            if query.message:
                await query.message.answer(
                    "Пришлите <b>одно голосовое</b> — распознаем в текст и сохраним как комментарий.\n"
                    "<code>/cancel</code> — отмена.",
                    parse_mode="HTML",
                )
            return
        if callback_data.action == "regenerate":
            await handle_callback(
                repo,
                query,
                {"action": "retry_nlp", "item_id": callback_data.item_id},
            )
            return
        await query.answer("Неизвестное действие", show_alert=True)

    @router.callback_query(AuthorDecisionAction.filter())
    async def on_author_decision_callback(
        query: CallbackQuery,
        callback_data: AuthorDecisionAction,
    ):
        uid = query.from_user.id if query.from_user else None
        if not is_bot_operator(uid):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if callback_data.action == "publish":
            await handle_callback(
                repo,
                query,
                {"action": "publish_now", "item_id": callback_data.item_id},
            )
            return
        if callback_data.action == "discard":
            await handle_callback(
                repo,
                query,
                {"action": "discard", "item_id": callback_data.item_id},
            )
            return
        if callback_data.action == "rewrite":
            await handle_author_rewrite(repo, query, callback_data.item_id)
            return
        await query.answer("Неизвестное действие", show_alert=True)

    @router.message(ReviewCommentForm.waiting_text, F.text)
    async def review_comment_receive(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        raw = (message.text or "").strip()
        if raw.lower() in {"/cancel", "отмена"}:
            await state.clear()
            await message.answer("Отменено.")
            return
        data = await state.get_data()
        item_id = data.get("review_item_id")
        await state.clear()
        if not item_id:
            await message.answer("Сессия устарела. Откройте «Ревью» снова.")
            return
        if not raw:
            await message.answer("Пустой текст. Введите комментарий или /cancel.")
            return
        merged_text = await save_owner_review_comment(
            repo,
            int(item_id),
            raw,
            user_id=uid,
            username=message.from_user.username if message.from_user else None,
        )
        banner = (
            "<b>Готово.</b> Ниже — карточка с <b>финальной версией</b> и действиями.\n\n"
            if merged_text
            else "<b>Комментарий сохранён.</b> Ниже — обновлённая карточка и доступные действия.\n\n"
        )
        await show_review_item_card(repo, int(item_id), message, banner=banner)

    @router.message(ReviewCoverForm.waiting_media)
    async def review_cover_receive(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        if message.text and message.text.strip().lower() in {"/cancel", "отмена"}:
            await state.clear()
            await message.answer("Отменено.")
            return
        data = await state.get_data()
        item_id = data.get("review_item_id")
        if not item_id:
            await state.clear()
            await message.answer("Сессия устарела. Откройте «Ревью» снова.")
            return
        try:
            cover_url, raw_feed_url, upload_error = await _store_review_cover(repo, message, int(item_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("cover upload failed item_id=%s", item_id)
            await message.answer(
                f"Не удалось сохранить обложку: {html.escape(str(exc))[:500]}\n\n"
                "Пришлите фото/изображение ещё раз или <code>/cancel</code>.",
                parse_mode="HTML",
            )
            return
        await state.clear()
        if raw_feed_url:
            media_status = (
                f"Для сайта записан <code>cover_image_url</code>: "
                f"<code>{html.escape(raw_feed_url)}</code>"
            )
        else:
            media_status = (
                "В <code>raw_feed</code> публичный URL не записан: локальный файл недоступен браузеру сайта. "
                "Нужно настроить <code>SITE_BASE_URL</code>/<code>MEDIA_UPLOAD_TOKEN</code> и endpoint сайта."
            )
            if upload_error:
                media_status += f"\nПричина upload: <code>{html.escape(upload_error)[:200]}</code>"
        await message.answer(
            f"Обложка сохранена для Telegram: <code>{html.escape(cover_url)}</code>\n"
            f"{media_status}\n"
            "Ниже — обновлённая карточка.",
            parse_mode="HTML",
        )
        await show_review_item_card(
            repo,
            int(item_id),
            message,
            banner="<b>Обложка добавлена.</b> Ниже — обновлённая карточка и действия.\n\n",
        )

    @router.message(ReviewVoiceForm.waiting_voice, F.voice)
    async def review_voice_receive(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        data = await state.get_data()
        item_id = data.get("review_item_id")
        if not item_id:
            await state.clear()
            await message.answer("Сессия устарела. Откройте «Ревью» снова.")
            return
        voice = message.voice
        if not voice:
            await message.answer("Нет вложения.")
            return
        bot = message.bot
        _, tmp_path = tempfile.mkstemp(suffix=".oga")
        path = Path(tmp_path)
        text = ""
        try:
            tg_file = await bot.get_file(voice.file_id)
            await bot.download(tg_file, destination=path)
            client = await get_openai_client()
            text = await client.transcribe_audio(path, lang=config.NL_LANG)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("voice comment failed item_id=%s", item_id)
            await message.answer(f"Не удалось обработать голосовое: {exc!s}"[:400])
            return
        finally:
            path.unlink(missing_ok=True)

        text = (text or "").strip()
        if not text:
            await message.answer(
                "Распознан пустой текст. Пришлите голосовое ещё раз или <code>/cancel</code>.",
                parse_mode="HTML",
            )
            return
        await state.clear()
        merged_text = await save_owner_review_comment(
            repo,
            int(item_id),
            text,
            user_id=uid,
            username=message.from_user.username if message.from_user else None,
        )
        banner = (
            "<b>Готово.</b> Ниже — карточка с <b>финальной версией</b> и действиями.\n\n"
            if merged_text
            else "<b>Голосовой комментарий сохранён.</b> Ниже — обновлённая карточка и доступные действия.\n\n"
        )
        await show_review_item_card(repo, int(item_id), message, banner=banner)

    @router.message(ReviewVoiceForm.waiting_voice)
    async def review_voice_expect_voice(message: Message):
        await message.answer(
            "Ожидается <b>голосовое сообщение</b> или <code>/cancel</code>.",
            parse_mode="HTML",
        )

    class _MessageCallbackProxy:
        def __init__(self, message: Message):
            self.message = message
            self.from_user = message.from_user

        async def answer(self, *args, **kwargs):
            kwargs.pop("show_alert", None)
            kwargs.pop("url", None)
            await self.message.answer(*args, **kwargs)

    @router.message(ReviewPublishScheduleForm.waiting_datetime, F.text)
    async def review_publish_schedule_receive(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        raw = (message.text or "").strip()
        if raw.lower() in {"/cancel", "отмена"}:
            await state.clear()
            await message.answer("Отменено.")
            return
        data = await state.get_data()
        item_id = data.get("review_item_id")
        if not item_id:
            await state.clear()
            await message.answer("Сессия устарела. Откройте «Ревью» снова.")
            return
        utc_dt, error = _parse_schedule_local_to_utc(raw)
        if error or utc_dt is None:
            await message.answer(
                f"{html.escape(error or 'Ошибка даты и времени')}\n"
                "Повторите ввод или <code>/cancel</code>.",
                parse_mode="HTML",
            )
            return
        await state.clear()
        proxy = _MessageCallbackProxy(message)
        await handle_callback(
            repo,
            proxy,  # type: ignore[arg-type]
            {
                "action": "publish_schedule",
                "item_id": int(item_id),
                "scheduled_at_utc": utc_dt.isoformat(),
            },
        )

    return router


__all__ = ["create_router"]
