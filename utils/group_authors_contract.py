"""Контракт group_authors + маппинг в лист user_messages."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from utils.channel_commenters_contract import (
    STATUS_COLLECTED,
    USER_MESSAGES_COLUMNS,
    USER_MESSAGES_SHEET_NAME,
    validate_user_messages_headers,
)

MESSAGE_TYPE_GROUP_AUTHOR = "group_message_author"

_MAX_TEXT_LEN = 500
_ID_SPLIT_RE = re.compile(r"[,;\s]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_author_id(*, group_id: str, user_id: str) -> str:
    base = f"{str(group_id).strip()}|{str(user_id).strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def parse_group_ids(raw: str | None) -> list[str]:
    """Разобрать список chat id: «3099…, id: 1 701 957 243» → целые числовые id."""
    text = str(raw or "").replace("\u00a0", " ").strip()
    if not text:
        return []
    found: list[str] = []
    for chunk in re.split(r"[,;]+", text):
        chunk = re.sub(r"(?i)\bid\s*:", " ", chunk)
        digits = re.sub(r"\D", "", chunk)
        if digits:
            found.append(digits)
    if not found:
        for part in _ID_SPLIT_RE.split(text):
            digits = re.sub(r"\D", "", part)
            if digits:
                found.append(digits)
    seen: set[str] = set()
    unique: list[str] = []
    for gid in found:
        if gid not in seen:
            seen.add(gid)
            unique.append(gid)
    return unique


def to_telethon_peer_id(group_id: str | int) -> int:
    """Положительный channel/megagroup id → peer id -100…; отрицательный — как есть."""
    raw = int(str(group_id).strip())
    if raw < 0:
        return raw
    as_str = str(raw)
    if as_str.startswith("100") and len(as_str) > 10:
        # Уже без минуса, но с префиксом 100…
        return -int(as_str) if not as_str.startswith("-") else raw
    return int(f"-100{raw}")


def build_user_messages_row(record: Mapping[str, Any]) -> dict[str, str]:
    """Строка user_messages: 1 автор × 1 группа (message_id = author_id)."""
    text = str(record.get("message_snippet") or record.get("comment_text") or "").strip()
    if len(text) > _MAX_TEXT_LEN:
        text = text[: _MAX_TEXT_LEN - 1] + "…"
    user_name = str(record.get("user_name") or "").strip()
    if not user_name and record.get("user_id"):
        user_name = str(record.get("user_id"))
    message_id = str(record.get("author_id") or record.get("message_id") or "").strip()
    related = str(
        record.get("group_id")
        or record.get("related_id")
        or record.get("post_id")
        or ""
    ).strip()
    return {
        "message_id": message_id,
        "user_id": str(record.get("user_id") or ""),
        "user_name": user_name,
        "related_id": related,
        "text": text,
        "message_type": str(
            record.get("message_type") or MESSAGE_TYPE_GROUP_AUTHOR
        ),
        "timestamp": str(
            record.get("last_message_at")
            or record.get("comment_at")
            or record.get("timestamp")
            or utc_now_iso()
        ),
        "status": str(record.get("status") or STATUS_COLLECTED),
    }


__all__ = [
    "MESSAGE_TYPE_GROUP_AUTHOR",
    "STATUS_COLLECTED",
    "USER_MESSAGES_COLUMNS",
    "USER_MESSAGES_SHEET_NAME",
    "build_user_messages_row",
    "make_author_id",
    "parse_group_ids",
    "to_telethon_peer_id",
    "utc_now_iso",
    "validate_user_messages_headers",
]
