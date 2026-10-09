"""Контракт channel_commenters + маппинг в лист user_messages."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Mapping

USER_MESSAGES_SHEET_NAME = "user_messages"

USER_MESSAGES_COLUMNS: tuple[str, ...] = (
    "message_id",
    "user_id",
    "user_name",
    "related_id",
    "text",
    "message_type",
    "timestamp",
    "status",
)

MESSAGE_TYPE_CHANNEL_COMMENT = "channel_comment"
STATUS_COLLECTED = "collected"

_MAX_TEXT_LEN = 500


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_commenter_id(*, channel_url: str, message_id: str | int) -> str:
    base = f"{channel_url.strip()}|{message_id}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def normalize_channel_url(url: str) -> str:
    text = (url or "").strip()
    if text.startswith("http"):
        return text.rstrip("/")
    if text.startswith("@"):
        return f"https://t.me/{text.lstrip('@')}"
    return text


def build_user_messages_row(record: Mapping[str, Any]) -> dict[str, str]:
    """Строка для листа user_messages."""
    text = str(record.get("comment_text") or "").strip()
    if len(text) > _MAX_TEXT_LEN:
        text = text[: _MAX_TEXT_LEN - 1] + "…"
    user_name = str(record.get("user_name") or "").strip()
    if not user_name and record.get("user_id"):
        user_name = str(record.get("user_id"))
    return {
        "message_id": str(record.get("message_id") or ""),
        "user_id": str(record.get("user_id") or ""),
        "user_name": user_name,
        "related_id": str(record.get("post_id") or record.get("related_id") or ""),
        "text": text,
        "message_type": MESSAGE_TYPE_CHANNEL_COMMENT,
        "timestamp": str(record.get("comment_at") or record.get("timestamp") or utc_now_iso()),
        "status": str(record.get("status") or STATUS_COLLECTED),
    }


def validate_user_messages_headers(header_row: list[str]) -> tuple[bool, list[str]]:
    """Проверка заголовков листа (допускает лишние колонки справа)."""
    header = [str(c).strip() for c in header_row if str(c).strip()]
    missing = [c for c in USER_MESSAGES_COLUMNS if c not in header]
    return (len(missing) == 0, missing)


__all__ = [
    "MESSAGE_TYPE_CHANNEL_COMMENT",
    "STATUS_COLLECTED",
    "USER_MESSAGES_COLUMNS",
    "USER_MESSAGES_SHEET_NAME",
    "build_user_messages_row",
    "make_commenter_id",
    "normalize_channel_url",
    "utc_now_iso",
    "validate_user_messages_headers",
]
