"""Services responsible for publishing approved items to Telegram."""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TYPE_CHECKING
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

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from aiogram import Bot as AiogramBot
else:  # pragma: no cover - fallback when aiogram is absent (e.g. unit tests)
    AiogramBot = Any

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
    """High-level orchestrator that publishes items into Telegram."""

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

    async def publish_pending(self, limit: int = 5) -> int:
        """Publish approved items and return number of successes."""

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            LOGGER.warning("CHANNEL_ID is not configured, skip publication queue")
            return 0

        items = await self._repository.list_publication_candidates(limit=limit)
        if not items:
            return 0

        published = 0
        for item in items:
            item_id = item["id"]
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
            except PublicationSendError as exc:
                await self._handle_failure(item_id, attempt_count + 1, str(exc.cause))
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Unexpected error while publishing item %s", item_id)
                await self._handle_failure(item_id, attempt_count + 1, str(exc))
            else:
                await self._handle_success(item_id, channel_id, message.message_id)
                published += 1
        return published

    async def _publish_item(self, channel_id: int | str, item: Mapping[str, Any]):
        item_id = item["id"]
        nlp = await self._repository.get_nlp_results(item_id) or {}
        text = self._build_caption(item, nlp)
        cover_url = self._extract_cover_url(nlp)
        method = self._bot.send_photo if cover_url else self._bot.send_message
        kwargs = {
            "chat_id": channel_id,
            "parse_mode": "HTML",
        }
        if cover_url:
            kwargs.update({"photo": cover_url, "caption": text})
        else:
            kwargs.update({"text": text, "disable_web_page_preview": False})

        result, _ = await _send_with_retry(
            method,
            attempts=self._immediate_attempts,
            delay_seconds=self._immediate_delay,
            **kwargs,
        )
        return result

    async def _handle_failure(self, item_id: int, attempt_number: int, error: str) -> None:
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

    async def _handle_success(
        self,
        item_id: int,
        channel_id: int | str,
        message_id: int,
    ) -> None:
        await self._repository.update_status(item_id, "published")
        await self._repository.save_publication(item_id, str(channel_id), str(message_id))
        await self._repository.log_event(
            item_id,
            "info",
            "publication_sent",
            {"message_id": message_id, "channel_id": channel_id},
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

    @staticmethod
    def _extract_cover_url(nlp: Mapping[str, Any]) -> str | None:
        extra = nlp.get("extra")
        if not isinstance(extra, Mapping):
            return None
        cover = extra.get("cover")
        if not isinstance(cover, Mapping):
            return None
        url = cover.get("url")
        return str(url) if isinstance(url, str) and url else None

    @staticmethod
    def _build_caption(item: Mapping[str, Any], nlp: Mapping[str, Any]) -> str:
        title = html.escape(str(item.get("title") or "Без заголовка"))
        merged = nlp.get("merged_text")
        summary = nlp.get("summary")
        body = merged or summary or item.get("content") or ""
        body_str = html.escape(str(body))
        link = item.get("link")
        parts = [f"<b>{title}</b>"]
        if body_str:
            parts.append(body_str)
        if link:
            parts.append(f"<a href=\"{html.escape(str(link))}\">Источник</a>")
        return "\n\n".join(parts)


__all__ = ["PublicationService", "PublicationSendError"]
