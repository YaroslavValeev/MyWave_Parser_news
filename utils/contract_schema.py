"""
CONTRACT-лист: единый источник правды по схеме raw_feed, ownership и валидации.
Версионируется; используется для создания/обновления листа CONTRACT в Google Sheets.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from utils.sheet_schema import DEFAULTS, RAW_FEED_COLUMNS

# Версия контракта (P0+P1). Bump при: добавлении/удалении полей raw_feed, смене ownership, изменении validation_rule.
CONTRACT_VERSION = "1.0.0"

# Колонки листа CONTRACT
CONTRACT_COLUMNS = [
    "field_name",
    "owner",
    "required_for",
    "validation_rule",
    "default_value",
    "notes",
    "contract_version",
    "updated_at",
]

# Владелец поля: BOT — пишет бот; SITE — пишет сайт (бот не перетирает).
OWNER_BOT = "BOT"
OWNER_SITE = "SITE"
OWNER_BOTH = "BOTH"  # оба могут читать/писать по контракту

# Область использования
REQ_INGEST = "INGEST"
REQ_PUBLISH = "PUBLISH"
REQ_WRITEBACK = "WRITEBACK"
REQ_ANALYTICS = "ANALYTICS"

# Поля, которые пишет только сайт (бот не перетирает при update)
# review_queue: бот ставит TRUE при вставке новой строки; сайт пишет FALSE после публикации — бот не перетирает
SITE_OWNED_FIELDS = {
    "canonical_url",
    "final_version",   # финальная версия поста — ownership SITE (по решению PM)
    "approved_by",
    "approved_at",
    "published_at",
    "review_queue",   # бот ставит TRUE на insert; сайт ставит FALSE после publish — при update бот не перетирает
    "cover_image_url",
    "final_posts",
    "publish_attempts",
    "publish_last_try_at",
    "publish_error",
    "publish_lock_by",
    "publish_lock_until",
}

# Поля, которые генерирует/пишет бот
BOT_OWNED_FIELDS = {
    "id",
    "slug",
    "source_type",
    "source_name",
    "source_url",
    "source_item_id",
    "created_at",
    "raw_title",
    "raw_content",
    "raw_html",
    "raw_media",
    "checksum",
    "row_number",
    "draft_version",   # опционально, бот может писать черновую версию
    "ingest_status",
    "ingest_last_try_at",
    "ingest_attempts",
    "ingest_error",
    "process_status",
    "processed_at",
    "process_error",
    "parse_error",
    "debug_info",
    "status",
    "content_format",
    "lang",
    "raw_tags",
    "media_json",
    "original_published_at",
    "scheduled_at",
    "text",
    "raw_id",
}

# Поля, используемые при ingest (критичные для P1)
INGEST_REQUIRED = {
    "id", "source_type", "source_name", "source_url", "raw_title", "raw_content",
    "checksum", "row_number", "created_at", "ingest_status", "source_item_id",
}

# Поля review workflow
REVIEW_FIELDS = {"review_queue", "approved_by", "approved_at", "draft_version"}


def _owner(field: str) -> str:
    if field in SITE_OWNED_FIELDS:
        return OWNER_SITE
    if field in BOT_OWNED_FIELDS:
        return OWNER_BOT
    return OWNER_BOTH


def _required_for(field: str) -> str:
    parts = []
    if field in INGEST_REQUIRED or field in BOT_OWNED_FIELDS:
        parts.append(REQ_INGEST)
    if field in SITE_OWNED_FIELDS or field in {"canonical_url", "final_version", "approved_by", "approved_at", "published_at"}:
        parts.append(REQ_PUBLISH)
    if field in {"canonical_url", "row_number", "final_version"}:
        parts.append(REQ_WRITEBACK)
    return ",".join(parts) if parts else REQ_ANALYTICS


def _validation_rule(field: str) -> str:
    if field == "row_number":
        return "обязателен при вставке; должен совпадать с номером строки в листе"
    if field == "checksum":
        return "SHA256 от канонического представления полей контента"
    if field == "source_item_id":
        return "уникален в рамках (source_type, source_name); приоритет идемпотентности"
    if field == "review_queue":
        return "true/false; бот ставит true для новых записей"
    if field in SITE_OWNED_FIELDS:
        return "заполняется сайтом; бот не перетирает"
    if field == "slug":
        return "генерируется ботом; уникальный идентификатор для URL"
    if field == "ingest_status":
        return "ok|skipped|error"
    return ""


def build_contract_rows() -> List[Dict[str, Any]]:
    """Строит строки для листа CONTRACT из RAW_FEED_COLUMNS и правил ownership."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for field_name in RAW_FEED_COLUMNS:
        default_val = DEFAULTS.get(field_name, "")
        if isinstance(default_val, (int, float)):
            default_val = str(default_val)
        notes = ""
        if field_name in REVIEW_FIELDS:
            notes = "Review workflow: бот пишет review_queue/draft_version; сайт — approved_by/approved_at."
        if field_name == "final_version":
            notes = "Финальная версия поста. Ownership SITE — бот не пишет."
        rows.append({
            "field_name": field_name,
            "owner": _owner(field_name),
            "required_for": _required_for(field_name),
            "validation_rule": _validation_rule(field_name),
            "default_value": "" if default_val is None else str(default_val),
            "notes": notes,
            "contract_version": CONTRACT_VERSION,
            "updated_at": now,
        })
    return rows
