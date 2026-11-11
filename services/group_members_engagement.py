"""Оркестрация сбора участников групп → SQLite → user_messages."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from collectors.group_members import GroupMembersCollectStats, collect_groups_members
from config.settings import config
from services.user_messages_sync import sync_records_to_user_messages
from storage.data import save_group_members
from utils.group_members_contract import build_user_messages_row, parse_group_targets

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GroupMembersRunResult:
    saved_db: int = 0
    sheet_updated: int = 0
    sheet_appended: int = 0
    stats: GroupMembersCollectStats | None = None
    errors: int = 0
    note: str = ""


def configured_group_ids() -> list[str]:
    raw = str(
        getattr(config, "GROUP_MEMBERS_IDS", None)
        or getattr(config, "GROUP_AUTHORS_IDS", "")
        or ""
    )
    return parse_group_targets(raw)


def _chunk_index_path() -> Path:
    db_path = Path(config.DB_PATH)
    parent = db_path.parent if db_path.parent.parts else Path(".")
    return parent / ".group_members_chunk_index"


def _read_chunk_index() -> int:
    path = _chunk_index_path()
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_chunk_index(value: int) -> None:
    _chunk_index_path().write_text(str(value), encoding="utf-8")


def _groups_chunk(all_ids: list[str]) -> list[str]:
    chunk = max(1, int(getattr(config, "GROUP_MEMBERS_CHANNELS_CHUNK", 1)))
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


async def sync_pending_group_members_to_sheet(*, limit: int = 500) -> dict[str, Any]:
    from storage.data import get_repository

    repo = await get_repository()
    rows = await repo.list_group_members_unsynced(limit=limit)
    if not rows:
        return {"db_rows": 0, "sheet": {}}
    for row in rows:
        row.setdefault("message_id", row.get("member_id"))
        row.setdefault("related_id", row.get("group_id"))
    sheet_stats = await sync_records_to_user_messages(
        rows,
        row_builder=build_user_messages_row,
    )
    successful = sheet_stats.get("updated", 0) + sheet_stats.get("appended", 0)
    if sheet_stats.get("errors", 0) == 0 and successful == len(rows):
        ids = [str(r.get("member_id") or "") for r in rows if r.get("member_id")]
        await repo.mark_group_members_synced(ids)
    return {"db_rows": len(rows), "sheet": sheet_stats}


async def run_group_members_collect(
    *,
    group_id: str | None = None,
    sync_sheet: bool = True,
    use_chunk: bool = True,
) -> GroupMembersRunResult:
    """Сбор участников: один group_id или chunk/весь список GROUP_MEMBERS_IDS."""
    result = GroupMembersRunResult(stats=GroupMembersCollectStats())
    session_manager = None
    try:
        client, session_manager = await _telegram_client()
    except Exception:
        LOGGER.exception("group_members: telegram client init failed")
        result.errors += 1
        if sync_sheet:
            try:
                pending = await sync_pending_group_members_to_sheet()
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
                LOGGER.exception("group_members: fallback db->sheet sync failed")
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
                result.note = "Список GROUP_MEMBERS_IDS / GROUP_AUTHORS_IDS пуст."
                return result
            targets = _groups_chunk(all_ids) if use_chunk else all_ids

        rows, st = await collect_groups_members(client, targets)
        result.stats = st
        if rows:
            result.saved_db = await save_group_members(rows)
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
                    ids = [r["member_id"] for r in rows if r.get("member_id")]
                    await repo.mark_group_members_synced(ids)
        result.errors += st.errors if st else 0
        notes: list[str] = []
        if st and st.skipped_no_access:
            notes.append(f"Нет доступа к группам: {st.skipped_no_access}.")
        if st and st.skipped_broadcast:
            notes.append(
                f"Пропущено каналов без discussion: {st.skipped_broadcast} "
                "(подписчики канала скрыты)."
            )
        if st and st.skipped_admin_required:
            notes.append(
                f"Нужны права админа для roster: {st.skipped_admin_required}."
            )
        result.note = " ".join(notes).strip()
    finally:
        if session_manager is not None:
            await session_manager.close_client()
    return result


__all__ = [
    "GroupMembersRunResult",
    "configured_group_ids",
    "run_group_members_collect",
    "sync_pending_group_members_to_sheet",
]
