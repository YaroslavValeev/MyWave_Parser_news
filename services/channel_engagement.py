"""Оркестрация сбора комментаторов каналов + SQLite + Sheets."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config.settings import config
from collectors.telegram_discussion import (
    EngagementCollectStats,
    collect_channel_comments,
    collect_channels_engagement,
)
from services.user_messages_sync import sync_records_to_user_messages, sync_unsynced_from_db
from storage.data import save_channel_commenters
from storage.sources import list_sources

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EngagementRunResult:
    saved_db: int = 0
    sheet_updated: int = 0
    sheet_appended: int = 0
    stats: EngagementCollectStats | None = None
    errors: int = 0
    note: str = ""


async def _telegram_client():
    from utils.telegram_session import TelegramSessionManager

    session_manager = TelegramSessionManager(
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE,
    )
    client = await session_manager.get_client()
    if client is None:
        raise RuntimeError("TelegramClient not initialized")
    return client, session_manager


def _engagement_chunk_index_path():
    from pathlib import Path

    db_path = Path(config.DB_PATH)
    parent = db_path.parent if db_path.parent.parts else Path(".")
    return parent / ".engagement_chunk_index"


def _read_engagement_chunk_index() -> int:
    path = _engagement_chunk_index_path()
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_engagement_chunk_index(value: int) -> None:
    _engagement_chunk_index_path().write_text(str(value), encoding="utf-8")


def _telegram_sources_chunk() -> list[dict[str, Any]]:
    chunk = max(1, int(getattr(config, "ENGAGEMENT_CHANNELS_CHUNK", 2)))
    tg = [
        {"url": s.url, "name": s.name or s.url, "type": s.type}
        for s in list_sources()
        if s.type == "telegram" and s.filter
    ]
    if not tg:
        return []
    idx = _read_engagement_chunk_index()
    num_chunks = max(1, (len(tg) + chunk - 1) // chunk)
    pos = idx % num_chunks
    start = pos * chunk
    end = min(start + chunk, len(tg))
    _write_engagement_chunk_index(idx + 1)
    return tg[start:end]


async def run_channel_engagement(
    *,
    channel_url: str | None = None,
    sync_sheet: bool = True,
) -> EngagementRunResult:
    """Сбор комментариев: один канал или chunk из sources."""
    result = EngagementRunResult(stats=EngagementCollectStats())
    session_manager = None
    try:
        client, session_manager = await _telegram_client()
    except Exception:
        LOGGER.exception("engagement: telegram client init failed")
        result.errors += 1
        if sync_sheet:
            try:
                pending = await sync_pending_commenters_to_sheet()
                sheet_stats = pending.get("sheet", {})
                result.sheet_updated = int(sheet_stats.get("updated", 0) or 0)
                result.sheet_appended = int(sheet_stats.get("appended", 0) or 0)
                result.errors += int(sheet_stats.get("errors", 0) or 0)
                db_rows = int(pending.get("db_rows", 0) or 0)
                if db_rows > 0:
                    result.note = (
                        "Не удалось подключиться к Telegram; выполнена только досинхронизация "
                        f"{db_rows} pending-записей из БД."
                    )
                else:
                    result.note = (
                        "Не удалось подключиться к Telegram; новых pending-записей в БД нет."
                    )
            except Exception:
                LOGGER.exception("engagement: fallback db->sheet sync failed")
                result.errors += 1
                result.note = (
                    "Не удалось подключиться к Telegram и выполнить fallback-синхронизацию."
                )
        else:
            result.note = "Не удалось подключиться к Telegram."
        return result
    try:
        if channel_url:
            rows, st = await collect_channel_comments(client, channel_url=channel_url)
        else:
            sources = _telegram_sources_chunk()
            if not sources:
                LOGGER.warning("engagement: no telegram sources")
                return result
            rows, st = await collect_channels_engagement(client, sources)
        result.stats = st
        if rows:
            result.saved_db = await save_channel_commenters(rows)
            if sync_sheet:
                sheet_stats = await sync_records_to_user_messages(rows)
                result.sheet_updated = sheet_stats.get("updated", 0)
                result.sheet_appended = sheet_stats.get("appended", 0)
                result.errors = sheet_stats.get("errors", 0)
                successful = result.sheet_updated + result.sheet_appended
                if result.errors == 0 and successful == len(rows):
                    from storage.data import get_repository

                    repo = await get_repository()
                    ids = [r["commenter_id"] for r in rows if r.get("commenter_id")]
                    await repo.mark_channel_commenters_synced(ids)
        result.errors += st.errors if st else 0
    finally:
        if session_manager is not None:
            await session_manager.close_client()
    return result


async def sync_pending_commenters_to_sheet(*, limit: int = 500) -> dict[str, Any]:
    return await sync_unsynced_from_db(limit=limit)


__all__ = [
    "EngagementRunResult",
    "run_channel_engagement",
    "sync_pending_commenters_to_sheet",
]
