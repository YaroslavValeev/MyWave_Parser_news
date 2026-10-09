"""Синхронизация channel_commenters → лист user_messages."""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from config.settings import config
from utils.channel_commenters_contract import (
    USER_MESSAGES_COLUMNS,
    USER_MESSAGES_SHEET_NAME,
    build_user_messages_row,
    validate_user_messages_headers,
)
from utils.sheet_gateway import get_worksheet, init_sheet_gateway, update_item

LOGGER = logging.getLogger(__name__)


def user_messages_sheet_name() -> str:
    return (
        str(getattr(config, "USER_MESSAGES_SHEET_NAME", "") or "").strip()
        or USER_MESSAGES_SHEET_NAME
    )


async def ensure_user_messages_headers(doc: Any) -> bool:
    sheet = user_messages_sheet_name()
    ws = get_worksheet(doc, sheet)
    if ws is None:
        try:
            from utils.channel_commenters_contract import USER_MESSAGES_COLUMNS

            ws = doc.add_worksheet(title=sheet, rows=500, cols=len(USER_MESSAGES_COLUMNS))
            ws.append_row(list(USER_MESSAGES_COLUMNS), value_input_option="RAW")
            LOGGER.info("Created sheet %s", sheet)
            return True
        except Exception:
            LOGGER.exception("cannot create sheet %s", sheet)
            return False
    try:
        header = ws.row_values(1)
        ok, missing = validate_user_messages_headers(header)
        if missing:
            for col in missing:
                ws.update_cell(1, len(header) + 1, col)
                header.append(col)
        return True
    except Exception:
        LOGGER.exception("ensure_user_messages_headers failed")
        return False


async def sync_records_to_user_messages(
    records: list[Mapping[str, Any]],
    *,
    lookup_field: str = "message_id",
) -> dict[str, int]:
    """Upsert строк в user_messages. Новые — append, существующие — update."""
    stats = {"input": len(records), "updated": 0, "appended": 0, "skipped": 0, "errors": 0}
    if os.getenv("PYTEST_CURRENT_TEST"):
        return stats
    if not records:
        return stats

    doc = await init_sheet_gateway()
    if not doc:
        stats["errors"] = len(records)
        return stats
    await ensure_user_messages_headers(doc)
    sheet = user_messages_sheet_name()

    to_append: list[dict[str, Any]] = []
    for rec in records:
        row = build_user_messages_row(rec)
        mid = str(row.get("message_id") or "").strip()
        if not mid:
            stats["skipped"] += 1
            continue
        if await update_item(doc, sheet, row, lookup_field=lookup_field):
            stats["updated"] += 1
        else:
            to_append.append(row)

    if to_append:
        try:
            ws = get_worksheet(doc, sheet)
            if ws is None:
                raise RuntimeError(f"worksheet not found: {sheet}")
            values = [
                [row.get(col, "") if row.get(col, "") is not None else "" for col in USER_MESSAGES_COLUMNS]
                for row in to_append
            ]
            ws.append_rows(values, value_input_option="RAW")
            n = len(values)
            stats["appended"] += n
        except Exception:
            LOGGER.exception("append user_messages failed")
            stats["errors"] += len(to_append)
    LOGGER.info("user_messages sync sheet=%s stats=%s", sheet, stats)
    return stats


async def sync_unsynced_from_db(*, limit: int = 500) -> dict[str, Any]:
    from storage.data import get_repository

    repo = await get_repository()
    rows = await repo.list_channel_commenters_unsynced(limit=limit)
    if not rows:
        return {"db_rows": 0, "sheet": {}}
    sheet_stats = await sync_records_to_user_messages(rows)
    successful = sheet_stats.get("updated", 0) + sheet_stats.get("appended", 0)
    if sheet_stats.get("errors", 0) == 0 and successful == len(rows):
        ids = [str(r.get("commenter_id") or "") for r in rows if r.get("commenter_id")]
        await repo.mark_channel_commenters_synced(ids)
    return {"db_rows": len(rows), "sheet": sheet_stats}


__all__ = [
    "ensure_user_messages_headers",
    "sync_records_to_user_messages",
    "sync_unsynced_from_db",
    "user_messages_sheet_name",
]
