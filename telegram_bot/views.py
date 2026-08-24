from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi


from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import config
from nlp.sanitize import sanitize_text
from nlp.openai_client import get_openai_client
from services.media_upload import maybe_autoupload_local_cover_and_sync_sheet, prepare_item_media_for_raw_feed
from services.raw_feed_sync import (
    sync_final_text,
    sync_media_fields,
    sync_nlp_state,
    sync_owner_comment,
    sync_publication_queue,
)
from storage.repository import AsyncNewsRepository
from utils.card_preview_text import PUBLIC_TITLE_MAX_LEN, lead_from_text, to_card_preview_text
from utils.telegram_editorial import format_telegram_editorial_html, hints_from_item
from utils.web_editorial import web_html_from_item
from utils.item_context import derive_item_title, get_item_text_context, is_title_only_summary_fallback
from utils.media_utils import (
    build_media_contract_diagnostic,
    extract_cover_image_url,
    extract_raw_feed_cover_image_url,
    iter_media_candidates,
    is_video_ref,
)
from utils.owner_content import ensure_merged_owner_post, ensure_owner_editing_context, owner_editing_text, strip_author_meta_labels
from utils.item_freshness import is_item_stale_for_review, review_max_age_days
from utils.russian_summary import ensure_russian_summary, is_probably_non_russian
from utils.status_labels_ru import format_status_counts_ru, status_label_ru
from utils.collect_report import format_collect_report_html, load_collect_report
from utils.telegram_session import TelegramSessionManager
from utils.video_providers import resolve_video_media

from .keyboards import ReviewAction, owner_review_card_markup

LOGGER = logging.getLogger(__name__)

# Сколько записей показывать в списке «Ревью» (review + new).
REVIEW_QUEUE_LIMIT = 20

# Лимит текста одного сообщения Telegram (HTML).
_TELEGRAM_HTML_SAFE_LEN = 4090
_REVIEW_MEDIA_GROUP_LIMIT = 10
_MEDIA_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")
_MEDIA_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_REVIEW_FALLBACK_TEXT_MAX = 6000


def _author_notes_text(nlp: Mapping[str, Any] | None) -> str:
    return str((nlp or {}).get("author_notes") or "").strip()


def _html_to_plain(raw: object) -> str:
    """Текст для превью: HTML/разметка сжимается в плоский текст."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        try:
            s = BeautifulSoup(s, "html.parser").get_text(separator="\n")
        except Exception:  # noqa: BLE001
            pass
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _truncate_plain(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if max_len <= 0 or len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _split_media_field(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _is_direct_http_media(url: str, *, media_type: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # Localhost media URLs are valid for the site in local dev, but Telegram
    # cannot fetch them from our machine, so review preview should fall back to
    # a local file or native Telegram download instead of sending the URL.
    if host in {"127.0.0.1", "localhost"}:
        return False
    if host in {"t.me", "telegram.me"}:
        return False
    path = (parsed.path or "").lower()
    if media_type == "photo":
        return any(path.endswith(ext) for ext in _MEDIA_IMAGE_EXTENSIONS)
    if media_type == "video":
        return any(path.endswith(ext) for ext in _MEDIA_VIDEO_EXTENSIONS)
    return False


def _local_media_path_from_public_url(url: str) -> Path | None:
    text = str(url or "").strip()
    if not text.startswith("/static/"):
        return None
    rel = text.removeprefix("/static/").lstrip("/")
    path = Path(rel)
    return path if path.is_file() else None


def _is_review_media_ref(url: str, *, media_type: str) -> bool:
    if _is_direct_http_media(url, media_type=media_type):
        return True
    path = _local_media_path_from_public_url(url)
    if path is None:
        return False
    suffix = path.suffix.lower()
    if media_type == "photo":
        return suffix in _MEDIA_IMAGE_EXTENSIONS
    if media_type == "video":
        return suffix in _MEDIA_VIDEO_EXTENSIONS
    return False


def _item_cover_url(item: Mapping[str, Any]) -> str:
    return extract_cover_image_url(
        {
            "cover_image_url": item.get("cover_image_url"),
            "image_url": item.get("image_url"),
            "images": item.get("images"),
            "raw_media": item.get("raw_media"),
            "media_json": item.get("media_json"),
            "source_url": item.get("link") or item.get("source_url"),
        },
        prefer_largest=True,
    )


def _item_raw_feed_cover_url(item: Mapping[str, Any]) -> str:
    return extract_raw_feed_cover_image_url(
        {
            "cover_image_url": item.get("cover_image_url"),
            "image_url": item.get("image_url"),
            "images": item.get("images"),
            "raw_media": item.get("raw_media"),
            "media_json": item.get("media_json"),
            "source_url": item.get("link") or item.get("source_url"),
        },
        prefer_largest=True,
    )


def _collect_review_media(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    media: list[tuple[str, str]] = []
    seen: set[str] = set()
    for media_type, field_name in (("photo", "images"), ("video", "videos")):
        for url in _split_media_field(item.get(field_name)):
            if url in seen or not _is_review_media_ref(url, media_type=media_type):
                continue
            media.append((media_type, url))
            seen.add(url)
            if len(media) >= _REVIEW_MEDIA_GROUP_LIMIT:
                return media
    for media_type, url in iter_media_candidates(item.get("media_json")):
        kind = "video" if is_video_ref(url, media_type=media_type) else "photo"
        if url in seen or not _is_review_media_ref(url, media_type=kind):
            continue
        media.append((kind, url))
        seen.add(url)
        if len(media) >= _REVIEW_MEDIA_GROUP_LIMIT:
            return media
    for media_type, url in iter_media_candidates(item.get("raw_media")):
        kind = "video" if is_video_ref(url, media_type=media_type) else "photo"
        if url in seen or not _is_review_media_ref(url, media_type=kind):
            continue
        media.append((kind, url))
        seen.add(url)
        if len(media) >= _REVIEW_MEDIA_GROUP_LIMIT:
            return media
    return media


def _pick_review_media(item: Mapping[str, Any]) -> tuple[str | None, str | None]:
    media = _collect_review_media(item)
    if not media:
        return None, None
    return media[0]


def _is_telegram_preview_candidate(item: Mapping[str, Any]) -> bool:
    link = str(item.get("link") or item.get("source_url") or "").strip()
    if not link.startswith(("http://", "https://")):
        return False
    host = (urlparse(link).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {"t.me", "telegram.me"}


def _parse_telegram_review_link(item: Mapping[str, Any]) -> tuple[str | int, int] | None:
    link = str(item.get("link") or item.get("source_url") or "").strip()
    if not link:
        return None
    parsed = urlparse(link)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"t.me", "telegram.me"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    msg_part = parts[-1]
    if not msg_part.isdigit():
        return None
    msg_id = int(msg_part)
    if parts[0] == "c" and len(parts) >= 3 and parts[1].isdigit():
        return (int(f"-100{parts[1]}"), msg_id)
    return (parts[0], msg_id)


def _review_media_cache_dir() -> Path:
    path = Path("downloads") / "review_media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _review_media_cache_prefix(item: Mapping[str, Any]) -> str:
    item_id = str(item.get("id") or "").strip()
    if item_id:
        return f"item-{item_id}"
    ref = str(item.get("link") or item.get("source_url") or "").strip()
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", ref).strip("-")
    return safe[:80] or "review-media"


def _find_cached_review_media(item: Mapping[str, Any]) -> Path | None:
    prefix = _review_media_cache_prefix(item)
    for path in sorted(_review_media_cache_dir().glob(f"{prefix}*")):
        if path.is_file() and path.suffix.lower() != ".json":
            return path
    return None


async def _download_telegram_review_media(item: Mapping[str, Any]) -> Path | None:
    cached = _find_cached_review_media(item)
    if cached is not None:
        return cached
    parsed = _parse_telegram_review_link(item)
    if parsed is None:
        return None
    if not (config.TELEGRAM_API_ID_USER and config.TELEGRAM_API_HASH_USER and config.TELEGRAM_PHONE):
        return None

    target, msg_id = parsed
    manager = TelegramSessionManager(
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE,
    )
    try:
        client = await manager.get_client()
        if client is None:
            return None
        entity = await asyncio.wait_for(client.get_entity(target), timeout=15.0)
        tg_message = await asyncio.wait_for(client.get_messages(entity, ids=msg_id), timeout=15.0)
        if not tg_message or not getattr(tg_message, "media", None):
            return None
        prefix = _review_media_cache_dir() / _review_media_cache_prefix(item)
        path = await asyncio.wait_for(
            tg_message.download_media(file=str(prefix)),
            timeout=max(10.0, float(getattr(config, "TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 90.0))),
        )
        if not isinstance(path, str) or not path.strip():
            return None
        resolved = Path(path)
        return resolved if resolved.exists() else None
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("telegram review media download failed: %s", exc)
        return None
    finally:
        await manager.close_client()


async def _download_telegram_review_text(item: Mapping[str, Any]) -> str | None:
    parsed = _parse_telegram_review_link(item)
    if parsed is None:
        return None
    if not (config.TELEGRAM_API_ID_USER and config.TELEGRAM_API_HASH_USER and config.TELEGRAM_PHONE):
        return None

    target, msg_id = parsed
    manager = TelegramSessionManager(
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE,
    )
    try:
        client = await manager.get_client()
        if client is None:
            return None
        entity = await asyncio.wait_for(client.get_entity(target), timeout=15.0)
        tg_message = await asyncio.wait_for(client.get_messages(entity, ids=msg_id), timeout=15.0)
        if not tg_message:
            return None
        text = str(
            getattr(tg_message, "raw_text", None)
            or getattr(tg_message, "message", None)
            or ""
        ).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("telegram review text fetch failed: %s", exc)
        return None
    finally:
        await manager.close_client()


def _extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path_parts = [part for part in (parsed.path or "").split("/") if part]
    if host == "youtu.be" and path_parts:
        return path_parts[0]
    if host == "youtube.com":
        if parsed.path == "/watch":
            for chunk in (parsed.query or "").split("&"):
                if chunk.startswith("v="):
                    return chunk.split("=", 1)[1]
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed"}:
            return path_parts[1]
    return ""


def _extract_fallback_text_from_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    candidates: list[str] = []
    for meta_name in ("description", "og:description", "twitter:description"):
        tag = soup.find("meta", attrs={"name": meta_name}) or soup.find(
            "meta", attrs={"property": meta_name}
        )
        if tag:
            candidates.append(sanitize_text(tag.get("content")))
    article = soup.find("article")
    if article:
        candidates.append(sanitize_text(article.get_text(separator="\n")))
    body = soup.body or soup
    candidates.append(sanitize_text(body.get_text(separator="\n")))
    for text in candidates:
        if text:
            return _truncate_plain(text, _REVIEW_FALLBACK_TEXT_MAX)
    return ""


async def _download_web_review_text(url: str) -> str | None:
    timeout = aiohttp.ClientTimeout(total=float(getattr(config, "WEBSITE_REQUEST_TIMEOUT", 60.0)))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru,en;q=0.9",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status >= 400:
                    return None
                html_text = await response.text(errors="ignore")
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("web review text fetch failed for %s: %s", url, exc)
        return None
    text = _extract_fallback_text_from_html(html_text)
    return text or None


async def _download_youtube_review_text(url: str) -> str | None:
    video_id = _extract_youtube_video_id(url)
    if not video_id:
        return None

    def _load_transcript() -> str:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru", "en"])
        joined = " ".join(str(chunk.get("text") or "").strip() for chunk in transcript)
        return sanitize_text(joined)

    try:
        text = await asyncio.to_thread(_load_transcript)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("youtube transcript fetch failed for %s: %s", url, exc)
        text = ""
    if text:
        return _truncate_plain(text, _REVIEW_FALLBACK_TEXT_MAX)
    return await _download_web_review_text(url)


async def _download_external_review_text(item: Mapping[str, Any]) -> str | None:
    link = str(item.get("link") or item.get("source_url") or "").strip()
    if not link.startswith(("http://", "https://")):
        return None
    host = (urlparse(link).hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return await _download_youtube_review_text(link)
    return await _download_web_review_text(link)


async def _send_native_telegram_media_preview(message: Message, item: Mapping[str, Any]) -> bool:
    cached = await _download_telegram_review_media(item)
    if cached is None:
        return False
    ext = cached.suffix.lower()
    media = FSInputFile(str(cached))
    try:
        if ext in _MEDIA_VIDEO_EXTENSIONS:
            await message.answer_video(media)
        else:
            await message.answer_photo(media)
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("native telegram review media preview failed for %s: %s", cached, exc)
        return False


def _review_media_input(url: str) -> FSInputFile | str:
    local_path = _local_media_path_from_public_url(url)
    return FSInputFile(str(local_path)) if local_path else url


async def _send_single_review_media_preview(message: Message, media_kind: str, media_url: str) -> None:
    media_input = _review_media_input(media_url)
    if media_kind == "photo":
        await message.answer_photo(media_input)
    elif media_kind == "video":
        await message.answer_video(media_input)


def _build_review_media_group(item: Mapping[str, Any]) -> list[InputMediaPhoto | InputMediaVideo]:
    payload: list[InputMediaPhoto | InputMediaVideo] = []
    for media_kind, media_url in _collect_review_media(item):
        media_input = _review_media_input(media_url)
        if media_kind == "video":
            payload.append(InputMediaVideo(media=media_input))
        else:
            payload.append(InputMediaPhoto(media=media_input))
    return payload


async def _send_review_media_preview(message: Message, item: Mapping[str, Any]) -> None:
    media = _collect_review_media(item)
    if not media:
        if _is_telegram_preview_candidate(item):
            if await _send_native_telegram_media_preview(message, item):
                return
            link = str(item.get("link") or item.get("source_url") or "").strip()
            try:
                await message.answer(f"Превью источника:\n{link}")
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("telegram source preview skipped for %s: %s", link, exc)
        return
    try:
        if len(media) == 1:
            media_kind, media_url = media[0]
            await _send_single_review_media_preview(message, media_kind, media_url)
            return
        await message.answer_media_group(_build_review_media_group(item))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("review media preview skipped for %s: %s", media, exc)
        media_kind, media_url = media[0]
        try:
            await _send_single_review_media_preview(message, media_kind, media_url)
        except Exception as inner_exc:  # noqa: BLE001
            LOGGER.debug("review single media fallback skipped for %s: %s", media_url, inner_exc)


def build_review_card_html(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
    *,
    link_as_anchor: bool = False,
    banner: str | None = None,
    content_excerpt_max: int = 3200,
    summary_max: int = 1800,
    editing_text: str | None = None,
    audit_logs: list[Mapping[str, Any]] | None = None,
) -> str:
    """Единый HTML карточки ревью: исходник + саммари + комментарий + ссылка."""
    nlp = nlp or {}
    extra = nlp.get("extra")
    display_title = ""
    if isinstance(extra, Mapping):
        display_title = str(extra.get("owner_display_title") or "").strip()
    title = html.escape(display_title or derive_item_title(item, max_len=160))
    final_text = str(nlp.get("merged_text") or "").strip()
    notes = _author_notes_text(nlp)
    link = str(item.get("link") or item.get("source_url") or "").strip()
    raw_content = editing_text or owner_editing_text(item, nlp) or item.get("content") or item.get("transcript") or ""
    original_content = item.get("content") or item.get("transcript") or ""
    show_original_fragment = bool(
        original_content
        and raw_content
        and is_probably_non_russian(str(original_content))
        and str(original_content).strip() != str(raw_content).strip()
    )
    summary_raw = str(nlp.get("summary") or "").strip()
    if summary_raw:
        summary_raw = ensure_russian_summary(
            summary_raw,
            item=item,
            source_text=get_item_text_context(item),
            lang=config.NL_LANG,
            max_len=summary_max,
        )
    summary_hidden = is_title_only_summary_fallback(item, nlp)

    preview_title = derive_item_title(item, max_len=PUBLIC_TITLE_MAX_LEN)
    preview_body = final_text or str(raw_content or "")
    preview_excerpt = to_card_preview_text(preview_body, max_len=260)
    preview_lead = lead_from_text(preview_body)
    media_diag = build_media_contract_diagnostic(item)
    video = resolve_video_media(item, poster_url=media_diag.cover_image_url)
    source_name = str(item.get("source") or item.get("source_name") or "").strip()

    ex_lim, sm_lim = int(content_excerpt_max), int(summary_max)
    text = ""
    for _ in range(24):
        parts: list[str] = []
        if banner:
            parts.append(banner)
        parts.append(f"<b>{title}</b>")
        parts.append("\n\n<b>Превью публикации</b>")
        parts.append(f"\ntitle: {html.escape(preview_title)}")
        parts.append(f"\nlead: {html.escape(preview_lead or '—')}")
        parts.append(f"\nexcerpt: {html.escape(preview_excerpt or '—')}")
        parts.append(f"\ncontent: {html.escape(_truncate_plain(preview_body, 400) or '—')}")
        cover_preview = media_diag.cover_image_url or _item_cover_url(item) or "—"
        parts.append(f"\ncover: {html.escape(str(cover_preview)[:160])}")
        parts.append(f"\nvideo_url: {html.escape(video.video_url or '—')}")
        parts.append(f"\nembed_url: {html.escape(video.embed_url or '—')}")
        parts.append(f"\nposter: {html.escape(video.poster_url or '—')}")
        parts.append(f"\nsource: {html.escape(source_name or '—')} · {html.escape(link or '—')}")
        media_line = f"\nmedia_status: <code>{html.escape(media_diag.media_status)}</code>"
        if media_diag.media_error:
            media_line += f" · error: <code>{html.escape(media_diag.media_error)}</code>"
        parts.append(media_line)
        editorial_nlp = dict(nlp) if isinstance(nlp, Mapping) else {}
        if summary_hidden:
            editorial_nlp["summary"] = ""
        parts.append(format_telegram_editorial_html(hints_from_item(item, editorial_nlp)))
        parts.append(web_html_from_item(item, editorial_nlp))
        if audit_logs:
            parts.append("\n\n<b>Audit</b>")
            for entry in audit_logs[:5]:
                parts.append(
                    f"\n· {html.escape(str(entry.get('created_at') or '')[:19])} "
                    f"<code>{html.escape(str(entry.get('message') or ''))}</code>"
                )
        cover_url = _item_cover_url(item)
        raw_feed_cover_url = _item_raw_feed_cover_url(item)
        if not cover_url:
            parts.append(
                "\n\n<b>Обложка</b>\n"
                "<i>для Telegram и сайта не найдена — прикрепите изображение кнопкой «Добавить/заменить обложку», "
                "иначе сайт покажет fallback.</i>"
            )
        elif not raw_feed_cover_url:
            parts.append(
                "\n\n<b>Обложка сайта</b>\n"
                "<i>картинка показана в Telegram, но у сайта пока нет публичного URL. "
                "Можно заменить/добавить обложку кнопкой ниже. Для появления на сайте нужен SITE_BASE_URL/MEDIA_UPLOAD_TOKEN "
                "или PUBLIC_MEDIA_BASE_URL; иначе сайт покажет fallback.</i>"
            )

        if final_text:
            parts.append(
                f"\n\n<b>Финальная версия</b>\n{html.escape(_truncate_plain(final_text, sm_lim))}"
            )
        elif notes:
            parts.append(
                "\n\n<i>Комментарий учтён — после сохранения будет собрана финальная версия без блока «Мнение автора».</i>"
            )

        excerpt = _truncate_plain(_html_to_plain(raw_content), ex_lim)
        if excerpt:
            excerpt_is_russian = not is_probably_non_russian(excerpt)
            if is_probably_non_russian(str(original_content or excerpt)) and excerpt_is_russian:
                label = "Текст для редактирования"
            elif is_probably_non_russian(str(original_content or excerpt)):
                label = "Исходный текст (оригинал — автоперевод недоступен)"
                if isinstance(extra, Mapping) and extra.get("translation_skipped") == "no_openai_key":
                    parts.append(
                        "\n\n⚠️ <i>Для перевода на русский задайте <code>OPENAI_API_KEY</code> в .env и перезапустите бота.</i>"
                    )
            else:
                label = "Исходный текст (фрагмент)"
            parts.append(f"\n\n<b>{label}</b>\n{html.escape(excerpt)}")
        else:
            parts.append(
                "\n\n<b>Исходный текст</b>\n"
                "<i>в базе пусто — откройте материал по ссылке «Источник».</i>"
            )
        if show_original_fragment:
            original_excerpt = _truncate_plain(_html_to_plain(original_content), min(ex_lim, 1200))
            if original_excerpt:
                parts.append(
                    f"\n\n<b>Оригинал (фрагмент)</b>\n{html.escape(original_excerpt)}"
                )

        summary_disp = _truncate_plain(summary_raw, sm_lim)
        if summary_disp and not summary_hidden:
            parts.append(f"\n\n<b>Саммари (NLP)</b>\n{html.escape(summary_disp)}")
        elif summary_hidden:
            parts.append(
                "\n\n<b>Саммари (NLP)</b>\n"
                "<i>скрыто: в базе нет текстового контекста, а текущее саммари было построено только по заголовку. "
                "Откройте «Источник» и при необходимости перегенерируйте материал вручную.</i>"
            )
        else:
            parts.append(
                "\n\n<b>Саммари (NLP)</b>\n"
                "<i>ещё не сгенерировано — нажмите «Перегенерировать NLP».</i>"
            )

        if link:
            if link_as_anchor:
                parts.append(f'\n\n<a href="{html.escape(link)}">Источник</a>')
            else:
                parts.append(f"\n\nИсточник:\n{html.escape(link)}")

        text = "".join(parts)
        if len(text) <= _TELEGRAM_HTML_SAFE_LEN:
            break
        ex_lim = max(400, ex_lim - 350)
        sm_lim = max(200, sm_lim - 250)
    else:
        LOGGER.warning("review card HTML still exceeds Telegram limit after shrink passes")
        parts_short = [
            f"<b>{title}</b>",
            (
                "\n\n<i>Сообщение слишком длинное для одного Telegram-текста. "
                "Полный материал — по ссылке «Источник».</i>"
            ),
            f"\n\n<b>Саммари (фрагмент)</b>\n{html.escape(_truncate_plain(summary_raw, 900))}",
        ]
        if notes:
            parts_short.append(f"\n\n<i>Мнение / комментарий:</i>\n{html.escape(_truncate_plain(notes, 400))}")
        if link:
            if link_as_anchor:
                parts_short.append(f'\n\n<a href="{html.escape(link)}">Источник</a>')
            else:
                parts_short.append(f"\n\nИсточник:\n{html.escape(link)}")
        text = "".join(parts_short)
    return text


def _owner_log_meta(query: CallbackQuery, action: str) -> dict[str, Any]:
    uid = query.from_user.id if query.from_user else None
    uname = query.from_user.username if query.from_user else None
    return {"action": action, "user_id": uid, "username": uname}


async def _finish_callback_message(query: CallbackQuery, text: str) -> None:
    try:
        await query.answer()
    except TelegramBadRequest as exc:
        if "query is too old" not in str(exc).lower() and "query id is invalid" not in str(exc).lower():
            raise
    if not query.message:
        return
    try:
        await query.message.edit_text(text)
    except TelegramBadRequest as exc:
        LOGGER.debug("edit_text skipped: %s", exc)
        await query.message.answer(text)


async def _offer_next_review(repo: AsyncNewsRepository, query: CallbackQuery) -> None:
    """После решения по карточке — предложить следующую из очереди или сообщить, что пусто."""
    if not query.message:
        return
    items = await repo.list_review_queue(limit=1)
    if not items:
        await query.message.answer("Очередь ревью пуста.")
        return
    it = items[0]
    tid = int(it["id"])
    title = derive_item_title(it, max_len=120)
    if len(title) > 120:
        title = title[:117] + "…"
    kb = InlineKeyboardBuilder()
    kb.button(
        text="▶️ Открыть следующую",
        callback_data=ReviewAction(action="open", item_id=tid).pack(),
    )
    await query.message.answer(
        f"<b>Следующая в очереди</b> <code>#{tid}</code>\n{html.escape(title)}",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


async def save_owner_review_comment(
    repo: AsyncNewsRepository,
    item_id: int,
    text: str,
    *,
    user_id: int | None,
    username: str | None,
) -> str | None:
    """Сохранить комментарий/мнение Owner (та же логика, что FSM «Комментарий» в роутере)."""
    await repo.upsert_author_notes(item_id, text.strip())
    await repo.log_event(
        item_id,
        "info",
        "owner_author_note",
        {"user_id": user_id, "username": username},
    )
    item = await repo.get_item(item_id)
    nlp = await repo.get_nlp_results(item_id) or {}
    if item:
        await sync_owner_comment(item, nlp)
    await maybe_autoupload_local_cover_and_sync_sheet(
        repo,
        item_id,
        user_id=user_id,
        username=username,
        trigger="owner_comment",
    )
    return await _generate_owner_final_text(
        repo,
        item_id,
        user_id=user_id,
        username=username,
        force=True,
    )


async def _generate_owner_final_text(
    repo: AsyncNewsRepository,
    item_id: int,
    *,
    user_id: int | None,
    username: str | None,
    force: bool = False,
) -> str | None:
    item = await repo.get_item(item_id)
    nlp = await repo.get_nlp_results(item_id) or {}
    if not item:
        return None
    item, nlp, _editing_text = await ensure_owner_editing_context(item, nlp)
    notes = str(nlp.get("author_notes") or "").strip()
    if not notes:
        return None
    if not force and str(nlp.get("merged_text") or "").strip():
        return str(nlp.get("merged_text") or "").strip()
    if not owner_editing_text(item, nlp) and not str(nlp.get("summary") or "").strip():
        return None

    merged_text = strip_author_meta_labels(
        await ensure_merged_owner_post(item, nlp, force=force)
    )
    if not merged_text:
        return None

    extra = dict(nlp.get("extra") or {}) if isinstance(nlp.get("extra"), Mapping) else {}
    if isinstance(nlp.get("extra"), Mapping) and nlp["extra"].get("owner_editing_text"):
        extra["owner_editing_text"] = nlp["extra"]["owner_editing_text"]

    await repo.save_nlp_results(
        item_id,
        summary=nlp.get("summary"),
        questions=nlp.get("questions"),
        decision=nlp.get("decision"),
        moderation=nlp.get("moderation"),
        extra=extra or nlp.get("extra"),
        merged_text=merged_text,
        voice_file=nlp.get("voice_file"),
        rewrite_guidance=nlp.get("rewrite_guidance"),
    )
    await repo.log_event(
        item_id,
        "info",
        "owner_final_text_generated",
        {"user_id": user_id, "username": username},
    )
    item_after = await repo.get_item(item_id) or item
    nlp_after = await repo.get_nlp_results(item_id) or {}
    await sync_final_text(item_after, nlp_after)
    return merged_text


async def _require_owner_comment(
    repo: AsyncNewsRepository,
    query: CallbackQuery,
    item_id: int,
    *,
    action: str,
) -> bool:
    nlp = await repo.get_nlp_results(item_id) or {}
    if _author_notes_text(nlp):
        return True
    await repo.log_event(
        item_id,
        "warning",
        "owner_action_blocked_missing_author_note",
        _owner_log_meta(query, action),
    )
    await query.answer(
        "Сначала добавьте ваш комментарий к материалу.",
        show_alert=True,
    )
    return False


def format_review_queue_summary(items: list[Mapping[str, Any]]) -> str:
    """Текст списка очереди ревью (HTML)."""
    lines = [f"<b>Очередь ревью</b> — материалов: {len(items)}", ""]
    for i, it in enumerate(items, 1):
        tid = it.get("id")
        title = derive_item_title(it, max_len=90)
        st = html.escape(status_label_ru(str(it.get("status") or "")))
        lines.append(f"{i}. <code>#{tid}</code> · <i>{st}</i>\n   {html.escape(title)}")
    lines.extend(["", "Нажмите «Открыть» под нужной строкой или снова «📋 Ревью» для обновления списка."])
    return "\n".join(lines)


def review_queue_keyboard(items: list[Mapping[str, Any]]):
    """Inline-кнопки «Открыть» по одной на материал."""
    builder = InlineKeyboardBuilder()
    for it in items:
        tid = int(it["id"])
        label = derive_item_title(it, max_len=40).replace("\n", " ")
        builder.button(
            text=f"▶️ #{tid} · {label}",
            callback_data=ReviewAction(action="open", item_id=tid).pack(),
        )
    builder.adjust(1)
    return builder.as_markup()


async def show_review_item(repo: AsyncNewsRepository, item_id: int, message: Message):
    await show_review_item_card(repo, item_id, message)


async def show_review_item_card(
    repo: AsyncNewsRepository,
    item_id: int,
    message: Message,
    *,
    banner: str | None = None,
):
    item = await repo.get_item(item_id)
    if not item:
        await message.answer("Материал не найден.")
        return
    if is_item_stale_for_review(item):
        await repo.expire_stale_review_items([item_id])
        await message.answer(
            f"Материал снят с ревью: дата публикации старше {review_max_age_days()} дн."
        )
        return
    fallback_text: str | None = None
    if not get_item_text_context(item):
        if _is_telegram_preview_candidate(item):
            fallback_text = await _download_telegram_review_text(item)
        else:
            fallback_text = await _download_external_review_text(item)
        if fallback_text:
            item = {**item, "content": fallback_text}
            await repo.update_item_content(item_id, fallback_text)
    nlp = await repo.get_nlp_results(item_id) or {}
    item, nlp, editing_text = await ensure_owner_editing_context(item, nlp)
    extra = nlp.get("extra")
    if isinstance(extra, Mapping) and (
        extra.get("owner_editing_text") or extra.get("owner_display_title")
    ):
        await repo.save_nlp_results(
            item_id,
            summary=nlp.get("summary"),
            questions=nlp.get("questions"),
            decision=nlp.get("decision"),
            moderation=nlp.get("moderation"),
            extra=extra,
            merged_text=nlp.get("merged_text"),
            voice_file=nlp.get("voice_file"),
            rewrite_guidance=nlp.get("rewrite_guidance"),
        )
    await _send_review_media_preview(message, item)
    audit_logs = await repo.fetch_recent_item_logs(item_id, limit=5, owner_only=True)
    text = build_review_card_html(
        item,
        nlp,
        link_as_anchor=False,
        banner=banner,
        editing_text=editing_text,
        audit_logs=audit_logs,
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=owner_review_card_markup(
            item_id,
            include_publish=True,
            has_cover=bool(_item_cover_url(item)),
        ),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def handle_callback(repo: AsyncNewsRepository, query: CallbackQuery, callback_data: dict):
    action = callback_data.get("action")
    raw_id = callback_data.get("item_id")
    item_id = int(raw_id) if raw_id is not None else 0
    if not action or not item_id:
        await query.answer("Некорректные данные", show_alert=True)
        return

    if action == "open":
        item = await repo.get_item(item_id)
        if not item:
            await query.answer("Материал не найден", show_alert=True)
            return
        await query.answer()
        if query.message:
            await show_review_item(repo, item_id, query.message)
        return

    if action == "approve":
        if not await _require_owner_comment(repo, query, item_id, action=action):
            return
        await repo.update_status(item_id, "approved")
        await repo.log_event(item_id, "info", "owner_approve", _owner_log_meta(query, action))
        item = await repo.get_item(item_id)
        nlp = await repo.get_nlp_results(item_id) or {}
        if item:
            await sync_publication_queue(item, nlp)
        await _finish_callback_message(query, "Одобрено")
        await _offer_next_review(repo, query)
        return

    if action == "retry_media":
        item = await repo.get_item(item_id)
        if not item:
            await query.answer("Материал не найден", show_alert=True)
            return
        prepared, upload_result = await prepare_item_media_for_raw_feed(item_id, item)
        if upload_result and upload_result.ok:
            prev_images = str(item.get("images") or "").strip()
            next_images = str(prepared.get("images") or "").strip()
            if next_images and next_images != prev_images:
                await repo.update_item_media(
                    item_id,
                    images=next_images,
                    videos=item.get("videos"),
                )
            item_after = await repo.get_item(item_id) or prepared
            synced = await sync_media_fields(item_after)
            await repo.log_event(
                item_id,
                "info",
                "owner_retry_media",
                {**_owner_log_meta(query, action), "cover_url": upload_result.url, "sheet_synced": synced},
            )
            await query.answer("Media обновлено", show_alert=False)
        elif upload_result is not None:
            await repo.log_event(
                item_id,
                "warning",
                "owner_retry_media_failed",
                {
                    **_owner_log_meta(query, action),
                    "error": upload_result.error,
                    "status_code": upload_result.status_code,
                },
            )
            await query.answer(f"Media ошибка: {upload_result.error}", show_alert=True)
        else:
            # Re-export current media diagnostic even without upload.
            await sync_media_fields(prepared)
            await repo.log_event(item_id, "info", "owner_retry_media", _owner_log_meta(query, action))
            await query.answer("Media пересинхронизировано", show_alert=False)
        if query.message:
            await show_review_item_card(repo, item_id, query.message, banner="🔁 Media retry")
        return

    if action == "discard":
        await repo.update_status(item_id, "discarded")
        await repo.log_event(item_id, "info", "owner_discard", _owner_log_meta(query, action))
        item = await repo.get_item(item_id)
        nlp = await repo.get_nlp_results(item_id) or {}
        if item:
            await sync_nlp_state(item, nlp, status="discarded")
        await _finish_callback_message(query, "Отклонено")
        await _offer_next_review(repo, query)
        return

    if action == "publish_now":
        if not await _require_owner_comment(repo, query, item_id, action=action):
            return
        await repo.update_schedule(item_id, None)
        await repo.update_status(item_id, "ready_to_publish")
        await repo.log_event(item_id, "info", "owner_publish_queue", _owner_log_meta(query, action))
        item = await repo.get_item(item_id)
        nlp = await repo.get_nlp_results(item_id) or {}
        if item:
            await sync_publication_queue(item, nlp)
        await _finish_callback_message(query, "Отправлено в публикацию")
        await _offer_next_review(repo, query)
        return

    if action == "publish_schedule":
        if not await _require_owner_comment(repo, query, item_id, action=action):
            return
        schedule_utc = str(callback_data.get("scheduled_at_utc") or "").strip()
        if not schedule_utc:
            await query.answer("Не указано время публикации", show_alert=True)
            return
        await repo.update_schedule(item_id, schedule_utc)
        await repo.update_status(item_id, "ready_to_publish")
        await repo.log_event(
            item_id,
            "info",
            "owner_publish_scheduled",
            {**_owner_log_meta(query, action), "scheduled_at_utc": schedule_utc},
        )
        item = await repo.get_item(item_id)
        nlp = await repo.get_nlp_results(item_id) or {}
        if item:
            await sync_publication_queue(item, nlp)
        await _finish_callback_message(
            query,
            f"Запланировано на {html.escape(schedule_utc)} UTC",
        )
        await _offer_next_review(repo, query)
        return

    if action == "defer":
        await repo.update_status(item_id, "deferred")
        await repo.log_event(item_id, "info", "owner_defer", _owner_log_meta(query, action))
        item = await repo.get_item(item_id)
        nlp = await repo.get_nlp_results(item_id) or {}
        if item:
            await sync_nlp_state(item, nlp, status="deferred")
        await _finish_callback_message(query, "Отложено")
        await _offer_next_review(repo, query)
        return

    if action == "open_source":
        item = await repo.get_item(item_id)
        link = (item or {}).get("link") or (item or {}).get("source_url") or ""
        link = str(link).strip()
        if not link:
            await query.answer("Ссылка на источник не указана", show_alert=True)
            return
        await repo.log_event(item_id, "info", "owner_open_source", _owner_log_meta(query, action))
        try:
            await query.answer(url=link)
        except TelegramBadRequest:
            await query.answer()
            if query.message:
                await query.message.answer(f"Источник:\n{html.escape(link)}", parse_mode="HTML")
        return

    if action == "retry_nlp":
        item = await repo.get_item(item_id)
        st = (item or {}).get("status") or ""
        if st not in ("review", "new", "deferred"):
            await query.answer(
                "Перегенерация доступна только для статусов review, new или deferred.",
                show_alert=True,
            )
            return
        from services.nlp_pipeline import reprocess_items

        await query.answer("Запускаю пересборку NLP…")
        try:
            n = await reprocess_items([item_id], repository=repo)
            await repo.log_event(
                item_id,
                "info",
                "owner_retry_nlp",
                {**_owner_log_meta(query, action), "reprocessed": n},
            )
            if query.message:
                await query.message.answer(
                    "Перегенерация NLP выполнена. Проверьте статус материала в «Статус» или откройте ревью снова."
                )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("retry_nlp failed item_id=%s", item_id)
            await repo.log_event(
                item_id,
                "error",
                "owner_retry_nlp_failed",
                {**_owner_log_meta(query, action), "error": str(exc)},
            )
            if query.message:
                await query.message.answer(f"Ошибка перегенерации: {exc!s}"[:500])
        return

    await query.answer("Действие не поддерживается", show_alert=True)


async def handle_author_rewrite(repo: AsyncNewsRepository, query: CallbackQuery, item_id: int) -> None:
    """Переписать саммари с учётом author_notes (кнопка «Переписать»)."""
    item = await repo.get_item(item_id)
    if not item:
        await query.answer("Материал не найден", show_alert=True)
        return
    nlp = await repo.get_nlp_results(item_id) or {}
    item, nlp, _editing_text = await ensure_owner_editing_context(item, nlp)
    source_text = owner_editing_text(item, nlp) or str(item.get("content") or "").strip()
    summary = str(nlp.get("summary") or "").strip()
    notes = str(nlp.get("author_notes") or "").strip()
    if not source_text and not summary:
        await query.answer("Нет исходного текста или саммари для финальной версии", show_alert=True)
        return
    if not notes:
        await query.answer(
            "Сначала добавьте комментарий (✍️ или 💬 Комментарий).",
            show_alert=True,
        )
        return
    await query.answer("Переписываю текст с учётом комментария…")
    merged_text = ""
    pytest_active = bool(os.getenv("PYTEST_CURRENT_TEST"))
    if getattr(config, "OPENAI_API_KEY", None) and not pytest_active:
        from nlp.openai_client import get_openai_client

        try:
            client = await get_openai_client()
            rewritten = await client.author_rewrite(
                source_text or summary,
                notes,
                base_summary=summary,
                lang=getattr(config, "NL_LANG", "ru"),
            )
            merged_text = strip_author_meta_labels(rewritten)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("handle_author_rewrite llm failed: %s", exc)
    if not merged_text:
        merged_text = strip_author_meta_labels(
            await ensure_merged_owner_post(item, nlp, force=True)
        )
    if not merged_text:
        if query.message:
            await query.message.answer("Не удалось собрать финальную версию.")
        return
    await repo.save_nlp_results(
        item_id,
        summary=nlp.get("summary"),
        questions=nlp.get("questions"),
        decision=nlp.get("decision"),
        moderation=nlp.get("moderation"),
        extra={
            **(nlp.get("extra") if isinstance(nlp.get("extra"), dict) else {}),
            "owner_rewritten": True,
        },
        merged_text=merged_text,
        voice_file=nlp.get("voice_file"),
        rewrite_guidance=nlp.get("rewrite_guidance"),
    )
    await repo.log_event(
        item_id,
        "info",
        "owner_author_rewrite",
        _owner_log_meta(query, "rewrite"),
    )
    if query.message:
        item2 = await repo.get_item(item_id)
        nlp2 = await repo.get_nlp_results(item_id) or {}
        if item2:
            await sync_final_text(item2, nlp2)
        banner = (
            "<b>Готово.</b> Ниже — карточка с <b>обновлённой финальной версией</b> (собрана на основе оригинала "
            "после вашего комментария).\n\n"
        )
        await show_review_item_card(repo, item_id, query.message, banner=banner)


def format_item_card(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> str:
    """Текст карточки для чата редакторов (HTML)."""
    return build_review_card_html(item, nlp, link_as_anchor=True)


def format_stats(
    counts: Mapping[str, int],
    metrics: Mapping[str, Any] | None,
    *,
    channel_commenters: int | None = None,
) -> str:
    """Краткая сводка: счётчики по статусам и очередь NLP (/stats, кнопка «Статус»)."""
    metrics = metrics or {}
    lines: list[str] = ["<b>📊 Статус</b> (кратко)", ""]
    lines.extend(format_status_counts_ru(counts))
    lines.extend(
        [
            "",
            "<b>Очередь NLP</b>",
            f"Ожидают обработки: {metrics.get('nlp_pending', 0)}",
            f"Сейчас в работе: {metrics.get('nlp_processing', 0)}",
        ]
    )
    if channel_commenters is not None:
        lines.extend(["", "<b>Комментаторы канала</b>", f"В базе: {channel_commenters}"])
    lines.append(format_collect_report_html(load_collect_report()))
    return "\n".join(lines)


def format_report(
    counts: Mapping[str, int],
    metrics: Mapping[str, Any] | None,
    *,
    publication_pending: int,
    channel_configured: bool,
) -> str:
    """Развёрнутый отчёт для /report: сводка + публикация + подсказки."""
    metrics = metrics or {}
    lines: list[str] = [
        "<b>📋 Отчёт</b> (/report)",
        "",
        "<b>Статусы материалов</b>",
    ]
    lines.extend(format_status_counts_ru(counts))
    lines.extend(
        [
            "",
            "<b>Очередь NLP</b>",
            f"Ожидают обработки: {metrics.get('nlp_pending', 0)}",
            f"Сейчас в работе: {metrics.get('nlp_processing', 0)}",
            "",
            "<b>Публикация</b>",
            f"В очереди на отправку: <b>{publication_pending}</b>",
        ]
    )
    if channel_configured:
        lines.append("Канал публикации задан (<code>CHANNEL_ID</code> / <code>TELEGRAM_CHANNEL_ID</code>).")
    else:
        lines.append(
            "⚠️ Канал публикации <b>не задан</b> в .env — команда /publish не отправит посты. "
            "Добавьте <code>CHANNEL_ID=-100…</code> или <code>TELEGRAM_CHANNEL_ID</code> и <b>перезапустите</b> бота."
        )
    lines.extend(
        [
            "",
            "<b>Ревью владельца</b>",
            "Публикация блокируется, пока у материала нет вашего комментария / мнения.",
        ]
    )
    err_n = int(counts.get("error", 0) or 0)
    if err_n:
        lines.extend(
            [
                "",
                "<b>Ошибки NLP</b>",
                f"Материалов с ошибкой: {err_n}. "
                "Частая причина — недоступный Moderations API; после обновления кода перезапустите бота "
                "или верните строки в статус «новые» для повторной обработки.",
            ]
        )
    lines.extend(
        [
            "",
            "<b>Источники RSS / YouTube</b>",
            "Счётчика «пустых лент» в БД нет — смотрите <b>лог сервера</b>: строки "
            "<code>RSS:</code>, <code>YouTube:</code>, <code>Feed HTTP</code>. "
            "При блокировке YouTube/RSS с этого IP задайте <code>HTTP_FEED_PROXY</code> в .env.",
        ]
    )
    lines.append(format_collect_report_html(load_collect_report()))
    return "\n".join(lines)


__all__ = [
    "REVIEW_QUEUE_LIMIT",
    "build_review_card_html",
    "format_item_card",
    "format_report",
    "format_review_queue_summary",
    "format_stats",
    "handle_author_rewrite",
    "handle_callback",
    "review_queue_keyboard",
    "save_owner_review_comment",
    "show_review_item",
    "show_review_item_card",
]
