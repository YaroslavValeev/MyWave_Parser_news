"""Legacy-compatible helpers built on top of the async repository."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from config.settings import config
from storage.repository import (
    AsyncNewsRepository,
    DuplicateItemError,
    initialize_database,
)

LOGGER = logging.getLogger(__name__)

DB_PATH = Path(config.DB_PATH)
_REPOSITORY = AsyncNewsRepository(DB_PATH)
_INITIALIZED = False
_INIT_LOCK = asyncio.Lock()


async def _ensure_initialized() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    async with _INIT_LOCK:
        if not _INITIALIZED:
            await initialize_database(DB_PATH)
            _INITIALIZED = True


async def save_news(news_items: Iterable[dict]) -> int:
    """Persist fetched news items and return number of inserted records."""
    await _ensure_initialized()
    saved = 0
    for item in news_items:
        payload = {
            "source": item.get("source", "unknown"),
            "title": item.get("title"),
            "content": item.get("content") or "",
            "link": item.get("link"),
            "date": _normalize_date(item.get("date")),
            "images": _join_media(item.get("images")),
            "videos": _join_media(item.get("videos")),
            "transcript": item.get("transcript"),
            "comment": item.get("comment"),
        }
        try:
            await _REPOSITORY.create_item(payload)
            saved += 1
        except DuplicateItemError:
            LOGGER.debug("Skip duplicate item with link %s", item.get("link"))
    if saved:
        LOGGER.info("Persisted %s new news items", saved)
    return saved


async def save_contacts(contacts: Iterable[dict]) -> int:
    """Persist extracted contacts and return number of affected rows."""

    await _ensure_initialized()
    normalized: dict[str, dict] = {}
    for contact in contacts:
        contact_id = str(contact.get("contact_id", "")).strip()
        if not contact_id:
            continue
        if contact_id in normalized:
            continue
        value = contact.get("value")
        if value is None or not str(value).strip():
            continue
        normalized[contact_id] = {
            "contact_id": contact_id,
            "source": contact.get("source") or "unknown",
            "type": contact.get("type") or "unknown",
            "value": str(value).strip(),
            "date_found": _normalize_date(contact.get("date_found")),
            "item_link": contact.get("item_link"),
            "item_id": contact.get("item_id"),
        }

    if not normalized:
        return 0

    inserted = await _REPOSITORY.upsert_contacts(normalized.values())
    if inserted:
        LOGGER.info("Persisted %s contact entries", inserted)
    return inserted


async def get_latest_news(limit: int = 10) -> list[dict]:
    await _ensure_initialized()
    return await _REPOSITORY.list_items(limit=limit)


async def clear_old_news(days: int = 30) -> int:
    await _ensure_initialized()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = await _REPOSITORY.delete_items_before(cutoff)
    if deleted:
        LOGGER.info("Removed %s stale news items", deleted)
    return deleted


async def get_repository() -> AsyncNewsRepository:
    """Вернуть инициализированный репозиторий для дальнейшей работы."""

    await _ensure_initialized()
    return _REPOSITORY


def save_news_sync(news_items: Iterable[dict]) -> int:
    """Synchronous wrapper for environments without event loop."""
    return asyncio.run(save_news(news_items))


def get_latest_news_sync(limit: int = 10) -> list[dict]:
    return asyncio.run(get_latest_news(limit))


def clear_old_news_sync(days: int = 30) -> int:
    return asyncio.run(clear_old_news(days))


def _normalize_date(raw_date: object) -> str | None:
    if raw_date is None:
        return None
    if isinstance(raw_date, datetime):
        return raw_date.isoformat()
    try:
        # attempt to parse ISO-like strings
        return datetime.fromisoformat(str(raw_date)).isoformat()
    except ValueError:
        return str(raw_date)


def _join_media(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return value.decode() if isinstance(value, bytes) else value
    if isinstance(value, Sequence):
        return "\n".join(str(item) for item in value)
    return str(value)


__all__ = [
    "save_news",
    "save_contacts",
    "get_latest_news",
    "clear_old_news",
    "get_repository",
    "save_news_sync",
    "get_latest_news_sync",
    "clear_old_news_sync",
]
