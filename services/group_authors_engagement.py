"""Оркестрация сбора авторов сообщений из групп → SQLite → user_messages."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import config
from collectors.group_authors import (
    GroupAuthorsCollectStats,
    collect_groups_authors,
)
from services.user_messages_sync import sync_records_to_user_messages
from storage.data import save_group_authors
from utils.group_authors_contract import (
    build_user_messages_row,
    parse_group_targets,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GroupAuthorsRunResult:
    saved_db: int = 0
    sheet_updated: int = 0
    sheet_appended: int = 0
    stats: GroupAuthorsCollectStats | None = None
    errors: int = 0
    note: str = ""


def configured_group_ids() -> list[str]:
    return parse_group_targets(str(getattr(config, "GROUP_AUTHORS_IDS", "") or ""))


def _chunk_index_path() -> Path:
    db_path = Path(config.DB_PATH)
    parent = db_path.parent if db_path.parent.parts else Path(".")
    return parent / ".group_authors_chunk_index"


def _read_chunk_index() -> int:
    path = _chunk_index_path()
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_chunk_index(value: int) -> None:
    _chunk_index_path().write_text(str(value), encoding="utf-8")


def _groups_chunk(all_ids: list[str]) -> list[str]:
    chunk = max(1, int(getattr(config, "GROUP_AUTHORS_CHANNELS_CHUNK", 1)))
    if not all_ids:
        return []
    idx = _read_chunk_index()
    num_chunks = max(1, (len(all_ids) + chunk - 1) // chunk)
    pos = idx % num_chunks
    start = pos * chunk
    end = min(start + chunk, len(all_ids))
    _write_chunk_index(idx + 1)
    return all_ids[start:end]


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


async def sync_pending_group_authors_to_sheet(*, limit: int = 500) -> dict[str, Any]:
    from storage.data import get_repository

    repo = await get_repository()
    rows = await repo.list_group_authors_unsynced(limit=limit)
    if not rows:
        return {"db_rows": 0, "sheet": {}}
    # Нормализуем поля под sheet builder
    for row in rows:
        row.setdefault("message_id", row.get("author_id"))
        row.setdefault("related_id", row.get("group_id"))
        row.setdefault("comment_text", row.get("message_snippet"))
        row.setdefault("comment_at", row.get("last_message_at"))
    sheet_stats = await sync_records_to_user_messages(
        rows,
        row_builder=build_user_messages_row,
    )
    successful = sheet_stats.get("updated", 0) + sheet_stats.get("appended", 0)
    if sheet_stats.get("errors", 0) == 0 and successful == len(rows):
        ids = [str(r.get("author_id") or "") for r in rows if r.get("author_id")]
        await repo.mark_group_authors_synced(ids)
    return {"db_rows": len(rows), "sheet": sheet_stats}


async def run_group_authors_collect(
    *,
    group_id: str | None = None,
    sync_sheet: bool = True,
    use_chunk: bool = True,
) -> GroupAuthorsRunResult:
    """Сбор авторов: один group_id или chunk/весь список из GROUP_AUTHORS_IDS."""
    result = GroupAuthorsRunResult(stats=GroupAuthorsCollectStats())
    session_manager = None
    try:
        client, session_manager = await _telegram_client()
    except Exception:
        LOGGER.exception("group_authors: telegram client init failed")
        result.errors += 1
        if sync_sheet:
            try:
                pending = await sync_pending_group_authors_to_sheet()
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
                LOGGER.exception("group_authors: fallback db->sheet sync failed")
                result.errors += 1
                result.note = (
                    "Не удалось подключиться к Telegram и выполнить fallback-синхронизацию."
                )
        else:
            result.note = "Не удалось подключиться к Telegram."
        return result

    try:
        from storage.data import get_repository

        repo = await get_repository()
        if group_id:
            targets = parse_group_targets(group_id) or [str(group_id).strip()]
        else:
            all_ids = configured_group_ids()
            if not all_ids:
                result.note = "Список GROUP_AUTHORS_IDS пуст."
                return result
            targets = _groups_chunk(all_ids) if use_chunk else all_ids

        checkpoints = await repo.get_group_authors_checkpoints(targets)
        rows, st, new_cps = await collect_groups_authors(
            client,
            targets,
            checkpoints=checkpoints,
        )
        result.stats = st
        if new_cps:
            await repo.upsert_group_authors_checkpoints(new_cps)
        if rows:
            result.saved_db = await save_group_authors(rows)
            if sync_sheet:
                sheet_stats = await sync_records_to_user_messages(
                    rows,
                    row_builder=build_user_messages_row,
                )
                result.sheet_updated = sheet_stats.get("updated", 0)
                result.sheet_appended = sheet_stats.get("appended", 0)
                result.errors = sheet_stats.get("errors", 0)
                successful = result.sheet_updated + result.sheet_appended
                if result.errors == 0 and successful == len(rows):
                    ids = [r["author_id"] for r in rows if r.get("author_id")]
                    await repo.mark_group_authors_synced(ids)
        result.errors += st.errors if st else 0
        if st and st.skipped_no_access:
            result.note = (
                (result.note + " " if result.note else "")
                + f"Нет доступа к группам: {st.skipped_no_access}."
            ).strip()
        if st and st.skipped_broadcast:
            result.note = (
                (result.note + " " if result.note else "")
                + f"Пропущено broadcast-каналов: {st.skipped_broadcast} "
                "(для них — /collect_commenters)."
            ).strip()
    finally:
        if session_manager is not None:
            await session_manager.close_client()
    return result


__all__ = [
    "GroupAuthorsRunResult",
    "configured_group_ids",
    "run_group_authors_collect",
    "sync_pending_group_authors_to_sheet",
]
