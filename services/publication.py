"""Services responsible for publishing queued items to Telegram."""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TYPE_CHECKING
from urllib.parse import urlparse
import aiosqlite
try:  # pragma: no cover - allow running tests without tenacity installed
    from tenacity import RetryError, retry, stop_after_attempt, wait_fixed
except ImportError:  # pragma: no cover - lightweight fallback
    class RetryError(Exception):
        def __init__(self, last_attempt):
            super().__init__("retry failed")
            self.last_attempt = last_attempt

    def stop_after_attempt(attempts: int):  # type: ignore[override]
        return attempts

    def wait_fixed(delay: float):  # type: ignore[override]
        return delay

    def retry(*, stop=None, wait=None, reraise=False):  # type: ignore[override]
        def decorator(func):
            async def wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    attempt = type(
                        "Attempt",
                        (),
                        {
                            "attempt_number": 1,
                            "exception": lambda self=exc: exc,
                        },
                    )()
                    raise RetryError(attempt) from exc

            return wrapper

        return decorator

from config.settings import config
from storage.repository import AsyncNewsRepository
from services.raw_feed_sync import sync_publication_result
from utils.card_preview_text import (
    normalize_public_title,
    normalize_publication_text,
    public_text_quality_issues,
    to_card_preview_text,
)
from utils.owner_content import build_fallback_merged_text, ensure_merged_owner_post, strip_author_meta_labels
from utils.item_context import (
    derive_item_title,
    get_item_text_context,
    is_title_only_summary_fallback,
    missing_text_context_summary,
)
from utils.media_utils import (
    is_image_ref,
    is_video_ref,
    iter_media_candidates,
    normalize_media_ref,
)

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from aiogram.types import FSInputFile as AiogramFSInputFile
    from aiogram import Bot as AiogramBot
else:  # pragma: no cover - fallback when aiogram is absent (e.g. unit tests)
    AiogramBot = Any
    AiogramFSInputFile = Any

try:  # pragma: no cover - import availability depends on test env
    from aiogram.types import FSInputFile
except Exception:  # noqa: BLE001
    FSInputFile = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""

    return datetime.now(timezone.utc)


class PublicationSendError(Exception):
    """Raised when the Telegram API keeps failing after retries."""

    def __init__(self, attempts: int, cause: BaseException):
        super().__init__(str(cause))
        self.attempts = attempts
        self.cause = cause


async def _send_with_retry(
    method,
    *,
    attempts: int,
    delay_seconds: float,
    **kwargs: Any,
):
    """Execute Telegram API call with tenacity-based retries."""

    attempts = max(1, attempts)
    delay_seconds = max(0.0, delay_seconds)
    counter = 0

    @retry(stop=stop_after_attempt(attempts), wait=wait_fixed(delay_seconds), reraise=True)
    async def _invoke():
        nonlocal counter
        counter += 1
        return await method(**kwargs)

    try:
        result = await _invoke()
        return result, counter
    except RetryError as exc:  # pragma: no cover - defensive logging
        cause = exc.last_attempt.exception() or exc
        raise PublicationSendError(counter, cause) from exc


class PublicationService:
    """High-level orchestrator that publishes queued items into Telegram."""

    def __init__(
        self,
        repository: AsyncNewsRepository,
        bot: AiogramBot,
        channel_id: str | int | None,
        *,
        now_func: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._bot = bot
        self._channel_id = channel_id
        self._now = now_func
        self._retry_minutes = config.PUBLICATION_RETRY_MINUTES
        self._max_attempts = max(1, config.PUBLICATION_MAX_ATTEMPTS)
        self._immediate_attempts = max(1, config.PUBLICATION_IMMEDIATE_RETRIES)
        self._immediate_delay = config.PUBLICATION_IMMEDIATE_DELAY_SECONDS
        self._retry_max_age_hours = max(0.0, float(config.PUBLICATION_RETRY_MAX_AGE_HOURS))

    async def publish_pending(self, limit: int = 5) -> int:
        """Publish queued items and return number of successes."""

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            LOGGER.warning(
                "CHANNEL_ID / TELEGRAM_CHANNEL_ID не задан в .env — очередь публикаций пропускается"
            )
            return 0

        fetch_limit = max(limit, limit * 20)
        items = await self._repository.list_publication_candidates(limit=fetch_limit)
        if not items:
            return 0

        published = 0
        considered = 0
        for item in items:
            if self._is_stale_retry_candidate(item):
                continue
            if self._is_scheduled_for_future(item):
                continue
            item_id = item["id"]
            nlp = await self._repository.get_nlp_results(item_id) or {}
            if not self._has_author_notes(nlp):
                await self._handle_missing_author_notes(item_id, str(item.get("status") or ""))
                continue
            if is_title_only_summary_fallback(item, nlp):
                await self._handle_untrusted_summary(item_id, str(item.get("status") or ""))
                continue
            merged_text = await ensure_merged_owner_post(item, nlp)
            if merged_text and merged_text != str(nlp.get("merged_text") or "").strip():
                await self._repository.save_nlp_results(
                    item_id,
                    summary=nlp.get("summary"),
                    questions=nlp.get("questions"),
                    decision=nlp.get("decision"),
                    moderation=nlp.get("moderation"),
                    extra=nlp.get("extra"),
                    merged_text=merged_text,
                    voice_file=nlp.get("voice_file"),
                    rewrite_guidance=nlp.get("rewrite_guidance"),
                )
                nlp = {**nlp, "merged_text": merged_text}
            quality_issues = self._publication_quality_issues(item, nlp)
            if quality_issues:
                await self._handle_needs_cleanup(item_id, str(item.get("status") or ""), quality_issues)
                continue
            considered += 1
            if considered > limit:
                break
            attempt_count, allowed, next_time = await self._should_attempt(item_id)
            if not allowed:
                if next_time is not None:
                    await self._repository.log_event(
                        item_id,
                        "info",
                        "publication_waiting",
                        {"next_attempt": next_time.isoformat()},
                    )
                continue

            try:
                message = await self._publish_item(channel_id, item)
            except PublicationSendError as err:
                await self._handle_failure(item_id, attempt_count + 1, str(err.cause), item=item)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception(
                    "Unexpected publication failure",
                    extra={"event": "publication_unexpected_error", "item_id": item_id},
                )
                await self._handle_failure(item_id, attempt_count + 1, str(exc), item=item)
            else:
                await self._handle_success(item_id, channel_id, message.message_id, item=item)
                published += 1
        return published

    def _is_scheduled_for_future(self, item: Mapping[str, Any]) -> bool:
        raw = str(item.get("scheduled_at") or "").strip()
        if not raw:
            return False
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        else:
            when = when.astimezone(timezone.utc)
        return when > self._now()

    def _is_stale_retry_candidate(self, item: Mapping[str, Any]) -> bool:
        if item.get("status") != "publish_retry":
            return False
        if self._retry_max_age_hours <= 0:
            return False
        raw_ts = item.get("updated_at") or item.get("created_at")
        if not raw_ts:
            return False
        try:
            ts = datetime.fromisoformat(str(raw_ts))
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        max_age = timedelta(hours=self._retry_max_age_hours)
        return self._now() - ts > max_age

    async def _publish_item(self, channel_id: int | str, item: Mapping[str, Any]):
        item_id = item["id"]
        nlp = await self._repository.get_nlp_results(item_id) or {}
        text = self._build_caption(item, nlp)
        cover_photo = self._extract_cover_photo(item, nlp)
        video_ref = self._extract_video_ref(item)
        # Telegram photo captions are much shorter than plain messages.
        use_photo = bool(cover_photo) and not video_ref and len(text) <= 900
        use_video = bool(video_ref) and len(text) <= 900
        if use_video:
            method = self._bot.send_video
        elif use_photo:
            method = self._bot.send_photo
        else:
            method = self._bot.send_message
        kwargs = {
            "chat_id": channel_id,
            "parse_mode": "HTML",
        }
        if use_video and video_ref:
            kwargs.update({"video": video_ref, "caption": text})
        elif use_photo and cover_photo:
            kwargs.update({"photo": cover_photo, "caption": text})
        else:
            kwargs.update({"text": text, "disable_web_page_preview": False})

        result, _ = await _send_with_retry(
            method,
            attempts=self._immediate_attempts,
            delay_seconds=self._immediate_delay,
            **kwargs,
        )
        return result

    async def _handle_failure(
        self,
        item_id: int,
        attempt_number: int,
        error: str,
        *,
        item: Mapping[str, Any] | None = None,
    ) -> None:
        if attempt_number >= self._max_attempts:
            await self._repository.update_status(item_id, "error")
            await self._repository.log_event(
                item_id,
                "error",
                "publication_failed",
                {"attempt": attempt_number, "error": error, "status": "stopped"},
            )
            await self._repository.log_event(
                item_id,
                "error",
                "publication_abandoned",
                {"attempt": attempt_number},
            )
        else:
            await self._repository.update_status(item_id, "publish_retry")
            await self._repository.log_event(
                item_id,
                "error",
                "publication_failed",
                {"attempt": attempt_number, "error": error},
            )
        sheet_item = item
        if sheet_item is None and hasattr(self._repository, "get_item"):
            try:
                sheet_item = await self._repository.get_item(item_id)
            except aiosqlite.Error:
                sheet_item = None
        nlp = await self._repository.get_nlp_results(item_id) or {}
        if sheet_item:
            await sync_publication_result(sheet_item, nlp, published=False, error=error)

    async def _handle_success(
        self,
        item_id: int,
        channel_id: int | str,
        message_id: int,
        *,
        item: Mapping[str, Any] | None = None,
    ) -> None:
        await self._repository.update_status(item_id, "published")
        await self._repository.save_publication(item_id, str(channel_id), str(message_id))
        await self._repository.log_event(
            item_id,
            "info",
            "publication_sent",
            {"message_id": message_id, "channel_id": channel_id},
        )
        sheet_item = item
        if sheet_item is None and hasattr(self._repository, "get_item"):
            try:
                sheet_item = await self._repository.get_item(item_id)
            except aiosqlite.Error:
                sheet_item = None
        nlp = await self._repository.get_nlp_results(item_id) or {}
        if sheet_item:
            await sync_publication_result(sheet_item, nlp, published=True)

    async def _handle_missing_author_notes(self, item_id: int, status_from: str) -> None:
        await self._repository.update_status(item_id, "review")
        await self._repository.log_event(
            item_id,
            "warning",
            "publication_blocked_missing_author_note",
            {"status_from": status_from, "required_action": "owner_comment"},
        )

    async def _handle_untrusted_summary(self, item_id: int, status_from: str) -> None:
        await self._repository.update_status(item_id, "review")
        await self._repository.log_event(
            item_id,
            "warning",
            "publication_blocked_untrusted_summary",
            {
                "status_from": status_from,
                "required_action": "open_source_and_rewrite",
            },
        )

    async def _handle_needs_cleanup(self, item_id: int, status_from: str, issues: list[str]) -> None:
        await self._repository.update_status(item_id, "review")
        await self._repository.log_event(
            item_id,
            "warning",
            "publication_blocked_needs_cleanup",
            {
                "status_from": status_from,
                "issues": issues,
                "required_action": "rewrite_or_regenerate",
            },
        )

    async def _should_attempt(self, item_id: int) -> tuple[int, bool, datetime | None]:
        last_failure = await self._repository.get_last_log(item_id, "publication_failed")
        if last_failure is None:
            return 0, True, None
        meta = last_failure.get("meta") or {}
        attempt = int(meta.get("attempt", 1))
        if attempt >= self._max_attempts:
            await self._repository.update_status(item_id, "error")
            await self._repository.log_event(
                item_id,
                "error",
                "publication_abandoned",
                {"attempt": attempt},
            )
            return attempt, False, None
        delay_minutes = self._retry_minutes[min(attempt - 1, len(self._retry_minutes) - 1)]
        if delay_minutes <= 0:
            return attempt, True, None
        try:
            last_time = datetime.fromisoformat(str(last_failure["created_at"]))
        except (KeyError, ValueError):  # pragma: no cover - defensive
            return attempt, True, None
        next_time = last_time + timedelta(minutes=delay_minutes)
        if self._now() < next_time:
            return attempt, False, next_time
        return attempt, True, None

    def _resolve_channel_id(self) -> int | str | None:
        if self._channel_id in (None, ""):
            return None
        try:
            return int(self._channel_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return self._channel_id

    @classmethod
    def _extract_cover_photo(cls, item: Mapping[str, Any], nlp: Mapping[str, Any]) -> str | AiogramFSInputFile | None:
        for key in ("cover_image_url", "image_url"):
            candidate = normalize_media_ref(item.get(key), media_type="image")
            photo = cls._cover_photo_input(candidate)
            if photo:
                return photo

        for key in ("images", "raw_media", "media_json"):
            for media_type, raw_candidate in iter_media_candidates(item.get(key)):
                candidate = normalize_media_ref(raw_candidate, media_type=media_type)
                if not candidate:
                    continue
                if not is_image_ref(candidate, media_type=media_type) or is_video_ref(
                    candidate,
                    media_type=media_type,
                ):
                    continue
                photo = cls._cover_photo_input(candidate)
                if photo:
                    return photo

        extra = nlp.get("extra")
        if not isinstance(extra, Mapping):
            return None
        cover = extra.get("cover")
        if not isinstance(cover, Mapping):
            return None
        url = cover.get("url")
        return cls._cover_photo_input(str(url)) if isinstance(url, str) and url else None

    @staticmethod
    def _cover_photo_input(cover_url: str | None) -> str | AiogramFSInputFile | None:
        if not cover_url:
            return None
        if cover_url.startswith("/static/"):
            rel = cover_url.removeprefix("/static/").lstrip("/")
            path = Path(rel)
            if path.is_file() and FSInputFile is not None:
                return FSInputFile(str(path))
            return None
        if cover_url.startswith(("http://", "https://")):
            parsed = urlparse(cover_url)
            host = (parsed.hostname or "").lower()
            if host in {"127.0.0.1", "localhost"}:
                if parsed.path.startswith("/static/"):
                    return PublicationService._cover_photo_input(parsed.path)
                return None
            return cover_url
        return cover_url

    @staticmethod
    def _has_author_notes(nlp: Mapping[str, Any]) -> bool:
        return bool(str(nlp.get("author_notes") or "").strip())

    @staticmethod
    def _publication_quality_issues(item: Mapping[str, Any], nlp: Mapping[str, Any]) -> list[str]:
        merged = str(nlp.get("merged_text") or "").strip()
        summary = str(nlp.get("summary") or "").strip()
        body = merged or summary or str(get_item_text_context(item) or "")
        clean_body = normalize_publication_text(body, preserve_paragraphs=True)
        title = normalize_public_title(derive_item_title(item, max_len=140))
        excerpt = to_card_preview_text(clean_body, max_len=260)
        return public_text_quality_issues(title=title, excerpt=excerpt, body=clean_body)

    @staticmethod
    def _extract_video_ref(item: Mapping[str, Any]) -> str | AiogramFSInputFile | None:
        for media_type, raw in iter_media_candidates(item.get("media_json")):
            if is_video_ref(raw, media_type=media_type):
                normalized = normalize_media_ref(raw)
                if normalized:
                    return PublicationService._cover_photo_input(normalized) or normalized
        for raw in str(item.get("videos") or "").splitlines():
            normalized = normalize_media_ref(raw.strip())
            if normalized and is_video_ref(normalized):
                return PublicationService._cover_photo_input(normalized) or normalized
        for media_type, raw in iter_media_candidates(item.get("raw_media")):
            if is_video_ref(raw, media_type=media_type):
                normalized = normalize_media_ref(raw)
                if normalized:
                    return PublicationService._cover_photo_input(normalized) or normalized
        return None

    @staticmethod
    def _build_caption(item: Mapping[str, Any], nlp: Mapping[str, Any]) -> str:
        summary = str(nlp.get("summary") or "").strip()
        notes = str(nlp.get("author_notes") or "").strip()
        merged_text = strip_author_meta_labels(str(nlp.get("merged_text") or "").strip())
        title = html.escape(normalize_public_title(derive_item_title(item, max_len=140)))

        parts: list[str] = []
        if is_title_only_summary_fallback(item, nlp):
            body = missing_text_context_summary(item)
            parts.append(f"<b>{title}</b>")
            if body:
                parts.append(html.escape(str(body)))
        elif summary and notes:
            # Канон Owner: саммари + почти сырой комментарий (без LLM-rewrite),
            # но нормализуем Markdown/артефакты одинаково, чтобы Telegram и сайт
            # не расходились по “визуальным” маркерам (заголовки #, акцент и т.п.).
            parts.append(f"<b>{title}</b>")
            parts.append(html.escape(normalize_publication_text(summary, preserve_paragraphs=True)))
            parts.append(html.escape(normalize_publication_text(notes, preserve_paragraphs=True)))
        elif merged_text:
            body = normalize_publication_text(merged_text, preserve_paragraphs=True)
            if body:
                parts.append(html.escape(body))
        elif notes:
            merged_fallback = build_fallback_merged_text(
                source_text=summary or get_item_text_context(item) or "",
                author_notes=notes,
                title=derive_item_title(item, max_len=140),
            )
            body = normalize_publication_text(merged_fallback, preserve_paragraphs=True)
            if body:
                parts.append(html.escape(body))
        else:
            parts.append(f"<b>{title}</b>")
            body = normalize_publication_text(
                summary or get_item_text_context(item) or "",
                preserve_paragraphs=True,
            )
            if body:
                parts.append(html.escape(body))

        site_url = str(getattr(config, "PUBLICATION_SITE_URL", "") or "https://mywavewake.ru/").strip()
        admin_url = str(
            getattr(config, "PUBLICATION_ADMIN_BOT_URL", "") or "https://t.me/MyWave_Admin_bot"
        ).strip()
        footer_bits: list[str] = []
        if site_url:
            footer_bits.append(f'<a href="{html.escape(site_url, quote=True)}">сайт</a>')
        if admin_url:
            footer_bits.append(f'<a href="{html.escape(admin_url, quote=True)}">тг-админ</a>')
        if footer_bits:
            parts.append(" · ".join(footer_bits))
        return "\n\n".join(p for p in parts if p)


__all__ = ["PublicationService", "PublicationSendError"]
