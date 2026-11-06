from __future__ import annotations

import hashlib
import json
from typing import Any

from utils.media_utils import validate_media_contract_fields

def generate_checksum(data):
    # Create a checksum based on the JSON-serialized data
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()


def ensure_raw_title(item_dict: dict[str, Any]) -> dict[str, Any]:
    """Гарантировать raw_title для header-based записи в Sheets."""
    out = dict(item_dict)
    if not (out.get("raw_title") or "").strip():
        out["raw_title"] = (out.get("title") or "").strip()
    return out


def normalize_ingest_status(raw: str | None) -> str:
    s = (raw or "ok").strip().lower()
    if s in ("ok", "error", "skipped", "raw"):
        return s
    return "ok"


def validate_status_consistency(row: dict[str, Any]) -> tuple[bool, str]:
    """Проверка сочетания ingest_status и публикуемого status (raw_feed)."""
    ing = (row.get("ingest_status") or "").strip().lower()
    st = (row.get("status") or "").strip().upper()
    if ing in ("error", "skipped") and st == "PUBLISHED":
        return False, "ingest error/skipped with PUBLISHED"
    return True, ""


def validate_raw_row(row: Any, strict: bool | None = None) -> bool | tuple[bool, str]:
    """
    Совместимость:
    - ``validate_raw_row(row)`` — bool (старый контракт: source_item_id, source, title).
    - ``validate_raw_row(row, strict=False)`` — (ok, err_msg) для ``storage/google_sheets.py``.
    """
    if strict is not None:
        if not isinstance(row, dict):
            return False, "not a dict"
        title = (row.get("raw_title") or row.get("title") or "").strip()
        if not title:
            return (False, "empty title") if strict else (True, "")
        media_ok, media_error = validate_media_contract_fields(row)
        if not media_ok:
            return False, media_error
        return True, ""

    required_fields = ["source_item_id", "source", "title"]
    return all(field in row for field in required_fields) and validate_media_contract_fields(row)[0]
