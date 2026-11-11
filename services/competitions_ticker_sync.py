"""Синхронизация листа competitions_ticker в Google Sheets (контракт Site v1)."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Mapping

from gspread.utils import rowcol_to_a1

from config.settings import config
from services.competitions_cache import invalidate_competitions_cache
from utils.competitions_contract import (
    ACCEPTANCE_TEST_IDS,
    COMPETITIONS_COLUMNS,
    acceptance_test_rows,
    normalize_competition_row,
    normalize_status,
    should_archive_row,
    utc_now_iso,
    validate_competition_row,
)
from utils.sheet_gateway import get_worksheet, init_sheet_gateway, update_item

LOGGER = logging.getLogger(__name__)


def competitions_sheet_name() -> str:
    return str(getattr(config, "COMPETITIONS_SHEET_NAME", "") or "competitions_ticker").strip()


async def _get_doc():
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    if not config.GOOGLE_SHEET_ID or not config.GOOGLE_CREDENTIALS_FILE:
        LOGGER.warning("competitions_ticker: GOOGLE_SHEET_ID or credentials not configured")
        return None
    return await init_sheet_gateway()


async def ensure_competitions_sheet_headers(doc) -> bool:
    """Создаёт лист и заголовки, если лист пустой; дописывает недостающие колонки."""
    sheet = competitions_sheet_name()
    try:
        ws = doc.worksheet(sheet)
    except Exception:
        try:
            ws = doc.add_worksheet(title=sheet, rows=500, cols=len(COMPETITIONS_COLUMNS))
            ws.append_row(list(COMPETITIONS_COLUMNS), value_input_option="RAW")
            LOGGER.info("Created sheet %s with %d columns", sheet, len(COMPETITIONS_COLUMNS))
            return True
        except Exception as exc:
            LOGGER.error("Failed to create sheet %s: %s", sheet, exc)
            return False

    try:
        header_row = ws.row_values(1)
    except Exception as exc:
        LOGGER.error("Cannot read header for %s: %s", sheet, exc)
        return False

    if not header_row:
        ws.append_row(list(COMPETITIONS_COLUMNS), value_input_option="RAW")
        LOGGER.info("Initialized empty sheet %s headers", sheet)
        return True

    missing = [c for c in COMPETITIONS_COLUMNS if c not in header_row]
    if missing:
        updated = header_row + missing
        last = rowcol_to_a1(1, len(updated))
        ws.update(f"A1:{last}", [updated], value_input_option="RAW")
        LOGGER.info("Added missing columns to %s: %s", sheet, ", ".join(missing))
    return True


def _row_to_cells(row: Mapping[str, Any], header: list[str]) -> list[str]:
    return [str(row.get(col) or "") for col in header]


async def upsert_competition_row(doc, row: Mapping[str, Any]) -> tuple[bool, str]:
    """Upsert одной строки по id. Возвращает (ok, action append|update|skip)."""
    normalized = normalize_competition_row(row)
    ok, reason = validate_competition_row(normalized)
    if not ok:
        LOGGER.warning(
            "competitions row skipped id=%s reason=%s",
            normalized.get("id"),
            reason,
        )
        return False, f"invalid:{reason}"

    sheet = competitions_sheet_name()
    updated = await update_item(doc, sheet, normalized, lookup_field="id")
    if updated:
        return True, "update"

    ws = get_worksheet(doc, sheet)
    if ws is None:
        return False, "no_worksheet"

    header = ws.row_values(1)
    if not header:
        await ensure_competitions_sheet_headers(doc)
        header = list(COMPETITIONS_COLUMNS)

    cells = _row_to_cells(normalized, header)
    ws.append_row(cells, value_input_option="RAW")
    LOGGER.info("competitions appended id=%s", normalized.get("id"))
    return True, "append"


async def upsert_competition_rows(
    rows: list[Mapping[str, Any]],
    *,
    invalidate_cache: bool = True,
) -> dict[str, int]:
    """Пакетный upsert. После успешных записей — invalidate кэша сайта."""
    stats = {"input": len(rows), "upserted": 0, "updated": 0, "appended": 0, "skipped": 0, "errors": 0}
    doc = await _get_doc()
    if not doc:
        stats["errors"] = len(rows)
        return stats

    await ensure_competitions_sheet_headers(doc)
    any_written = False

    for row in rows:
        try:
            ok, action = await upsert_competition_row(doc, row)
        except Exception:
            LOGGER.exception("competitions upsert failed id=%s", row.get("id"))
            stats["errors"] += 1
            continue
        if not ok:
            stats["skipped"] += 1
            continue
        stats["upserted"] += 1
        any_written = True
        if action == "update":
            stats["updated"] += 1
        elif action == "append":
            stats["appended"] += 1

    if any_written and invalidate_cache:
        await invalidate_competitions_cache(reason="competitions_bulk_upsert")

    LOGGER.info(
        "competitions_ticker sync done sheet=%s stats=%s",
        competitions_sheet_name(),
        stats,
    )
    return stats


async def archive_past_competitions(*, today: date | None = None) -> int:
    """Переводит строки с end_date < today в ARCHIVED."""
    today = today or datetime.now(timezone.utc).date()
    doc = await _get_doc()
    if not doc:
        return 0

    sheet = competitions_sheet_name()
    ws = get_worksheet(doc, sheet)
    if ws is None:
        return 0

    try:
        records = ws.get_all_records()
    except Exception:
        LOGGER.exception("cannot read %s for archive", sheet)
        return 0

    archived = 0
    now = utc_now_iso()
    for rec in records:
        comp_id = str(rec.get("id") or "").strip()
        if not comp_id:
            continue
        if not should_archive_row(rec, today=today):
            continue
        if normalize_status(rec.get("status")) == "ARCHIVED":
            continue
        payload = normalize_competition_row({**rec, "status": "ARCHIVED", "updated_at": now})
        if await update_item(doc, sheet, payload, lookup_field="id"):
            archived += 1

    if archived:
        await invalidate_competitions_cache(reason="competitions_archive_past")
    LOGGER.info("competitions archived past events count=%s", archived)
    return archived


async def sync_acceptance_test_rows(*, invalidate_cache: bool = True) -> dict[str, int]:
    """Записывает 3 тестовые строки приёмки (test-1 … test-3)."""
    return await upsert_competition_rows(
        acceptance_test_rows(),
        invalidate_cache=invalidate_cache,
    )


async def archive_acceptance_test_rows(*, invalidate_cache: bool = True) -> dict[str, int]:
    """Скрывает синтетику приёмки: test-1…test-3 → status ARCHIVED (поля события сохраняются)."""
    now = utc_now_iso()
    doc = await _get_doc()
    existing: dict[str, Mapping[str, Any]] = {}
    if doc:
        sheet = competitions_sheet_name()
        ws = get_worksheet(doc, sheet)
        if ws is not None:
            try:
                for rec in ws.get_all_records():
                    cid = str(rec.get("id") or "").strip()
                    if cid in ACCEPTANCE_TEST_IDS:
                        existing[cid] = rec
            except Exception:
                LOGGER.exception("cannot read %s for archive_acceptance", sheet)

    rows: list[dict[str, Any]] = []
    for comp_id in sorted(ACCEPTANCE_TEST_IDS):
        base = dict(existing.get(comp_id) or {})
        if not base:
            base = {
                "id": comp_id,
                "discipline": "both",
                "event_name": f"Parser Acceptance {comp_id}",
                "location": "—",
                "country": "—",
                "start_date": "2020-01-01",
                "end_date": "2020-01-01",
                "event_url": "https://example.com/archived",
                "source_name": "parser_acceptance",
                "source_url": "https://example.com/archived",
            }
        base["status"] = "ARCHIVED"
        base["updated_at"] = now
        base["ingest_status"] = str(base.get("ingest_status") or "archived_acceptance")
        rows.append(normalize_competition_row(base))
    return await upsert_competition_rows(rows, invalidate_cache=invalidate_cache)


async def run_competitions_maintenance(
    rows: list[Mapping[str, Any]] | None = None,
    *,
    archive: bool = True,
    seed_acceptance: bool = False,
) -> dict[str, Any]:
    """Полный цикл: опционально seed приёмки, upsert rows, архивация прошедших."""
    result: dict[str, Any] = {"archive_count": 0, "upsert": {}}
    if seed_acceptance:
        result["acceptance"] = await sync_acceptance_test_rows()
    if rows:
        result["upsert"] = await upsert_competition_rows(rows)
    elif not seed_acceptance:
        result["upsert"] = {"input": 0, "upserted": 0}
    if archive:
        result["archive_count"] = await archive_past_competitions()
    return result


__all__ = [
    "archive_acceptance_test_rows",
    "archive_past_competitions",
    "competitions_sheet_name",
    "ensure_competitions_sheet_headers",
    "run_competitions_maintenance",
    "sync_acceptance_test_rows",
    "upsert_competition_row",
    "upsert_competition_rows",
]
