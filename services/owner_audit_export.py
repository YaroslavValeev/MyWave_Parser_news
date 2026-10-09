"""
Пакетный экспорт действий Owner из SQLite (`logs.message` LIKE 'owner_%')
в лист Google Sheets `admin_actions__review` без вызова API на каждый клик.

При сбое Sheets — запись CSV в `data/exports/` и продвижение чекпоинта,
если CSV сохранён (идемпотентность по `log_id`).
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config.settings import config
from storage.repository import AsyncNewsRepository

LOGGER = logging.getLogger(__name__)

CURSOR_KEY = "owner_actions_last_log_id"

# Заголовки листа admin_actions__review (совместить с таблицей-источником истины при расхождении)
ADMIN_ACTIONS_COLUMNS = [
    "log_id",
    "created_at",
    "item_id",
    "action",
    "level",
    "user_id",
    "username",
    "meta_json",
]


@dataclass(slots=True)
class OwnerAuditExportResult:
    exported: int
    sheets_written: bool
    csv_path: str | None
    new_cursor: int
    error: str | None = None


def _row_to_sheet_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    meta = row.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, json.JSONDecodeError):
            meta = {}
    uid = meta.get("user_id") if isinstance(meta, dict) else None
    uname = meta.get("username") if isinstance(meta, dict) else None
    return {
        "log_id": row.get("id"),
        "created_at": row.get("created_at") or "",
        "item_id": row.get("item_id") if row.get("item_id") is not None else "",
        "action": row.get("message") or "",
        "level": row.get("level") or "",
        "user_id": uid if uid is not None else "",
        "username": uname if uname is not None else "",
        "meta_json": json.dumps(meta, ensure_ascii=False) if meta else "",
    }


def _write_csv(rows: list[dict[str, Any]]) -> Path:
    db_path = Path(config.DB_PATH).resolve()
    exports_dir = (db_path.parent if db_path.is_file() else db_path) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = exports_dir / f"owner_audit_{ts}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ADMIN_ACTIONS_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


async def _append_to_google_sheets(rows: list[dict[str, Any]]) -> bool:
    if not config.GOOGLE_CREDENTIALS_FILE or not config.GOOGLE_SHEET_ID:
        LOGGER.warning("owner_audit_export: GOOGLE_CREDENTIALS_FILE или GOOGLE_SHEET_ID не заданы, пропуск Sheets")
        return False

    from utils.import_asyncio import init_google_sheets

    doc = await init_google_sheets()
    if not doc:
        return False

    sheet_name = (config.ADMIN_ACTIONS_SHEET_NAME or "admin_actions__review").strip()
    try:
        ws = doc.worksheet(sheet_name)
    except Exception:
        ws = doc.add_worksheet(title=sheet_name, rows=3000, cols=len(ADMIN_ACTIONS_COLUMNS) + 2)

    try:
        allv = ws.get_all_values()
        if not allv:
            ws.append_row(ADMIN_ACTIONS_COLUMNS)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("owner_audit_export: проверка заголовка: %s", exc)
        ws.append_row(ADMIN_ACTIONS_COLUMNS)

    value_rows = [[r.get(c, "") for c in ADMIN_ACTIONS_COLUMNS] for r in rows]
    if value_rows:
        ws.append_rows(value_rows, value_input_option="RAW")
    return True


async def run_owner_audit_export(repository: AsyncNewsRepository) -> OwnerAuditExportResult:
    """
    Выгрузить новые записи owner_* после чекпоинта в Sheets и/или CSV.
    Чекпоинт продвигается, если данные сохранены хотя бы в одном приёмнике.
    """
    last = await repository.get_audit_export_cursor(CURSOR_KEY)
    batch = await repository.fetch_owner_action_logs_after(last, limit=2000)
    if not batch:
        return OwnerAuditExportResult(
            exported=0,
            sheets_written=False,
            csv_path=None,
            new_cursor=last,
            error=None,
        )

    sheet_dicts = [_row_to_sheet_dict(r) for r in batch]
    max_id = max(int(r["id"]) for r in batch)

    sheets_ok = False
    err_sheets: str | None = None
    try:
        sheets_ok = await _append_to_google_sheets(sheet_dicts)
    except Exception as exc:  # noqa: BLE001
        err_sheets = str(exc)
        LOGGER.exception("owner_audit_export: ошибка Google Sheets: %s", exc)

    csv_path: Path | None = None
    err_csv: str | None = None
    if not sheets_ok:
        try:
            csv_path = await asyncio.to_thread(_write_csv, sheet_dicts)
            LOGGER.info("owner_audit_export: записан CSV fallback %s", csv_path)
        except Exception as exc:  # noqa: BLE001
            err_csv = str(exc)
            LOGGER.exception("owner_audit_export: ошибка CSV fallback: %s", exc)

    persisted = sheets_ok or (csv_path is not None)
    new_cursor = max_id if persisted else last
    if persisted:
        await repository.set_audit_export_cursor(CURSOR_KEY, new_cursor)

    err = None
    if not persisted:
        err = "; ".join(filter(None, [err_sheets, err_csv])) or "export failed"

    return OwnerAuditExportResult(
        exported=len(batch),
        sheets_written=sheets_ok,
        csv_path=str(csv_path) if csv_path else None,
        new_cursor=new_cursor,
        error=err,
    )


__all__ = [
    "ADMIN_ACTIONS_COLUMNS",
    "CURSOR_KEY",
    "OwnerAuditExportResult",
    "run_owner_audit_export",
]


async def _cli_main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from storage.data import get_repository

    repo = await get_repository()
    result = await run_owner_audit_export(repo)
    print(
        f"exported={result.exported} sheets={result.sheets_written} "
        f"csv={result.csv_path} cursor={result.new_cursor} err={result.error}"
    )


if __name__ == "__main__":
    asyncio.run(_cli_main())
