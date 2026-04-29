import hashlib
import json

VALID_INGEST_STATUSES = {"new", "raw", "ok", "skipped", "error", "processed"}


def generate_checksum(data):
    json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(json_str.encode("utf-8")).hexdigest()


def ensure_raw_title(row):
    normalized = dict(row)
    if not str(normalized.get("raw_title") or "").strip():
        normalized["raw_title"] = (
            normalized.get("title")
            or normalized.get("summary")
            or normalized.get("raw_content")
            or "Без заголовка"
        )
    return normalized


def normalize_ingest_status(status):
    value = str(status or "ok").strip().lower()
    aliases = {"done": "ok", "success": "ok", "failed": "error"}
    value = aliases.get(value, value)
    return value if value in VALID_INGEST_STATUSES else "ok"


def validate_raw_row(row, strict=None):
    required_fields = ["id", "source_url", "raw_content", "checksum"]
    missing = [field for field in required_fields if not row.get(field)]
    if missing:
        message = "missing required fields: " + ", ".join(missing)
        return (False, message) if strict is not None else False
    return (True, "") if strict is not None else True


def validate_status_consistency(row):
    ingest_status = normalize_ingest_status(row.get("ingest_status"))
    status = str(row.get("status") or "").upper()
    if ingest_status in {"skipped", "error"} and status == "PUBLISHED":
        return False, "ingest_status cannot be skipped/error when status is PUBLISHED"
    return True, ""
