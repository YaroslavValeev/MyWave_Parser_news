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
_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)",
    re.I,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_author_id(*, group_id: str, user_id: str) -> str:
    base = f"{str(group_id).strip()}|{str(user_id).strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def normalize_group_target(raw: str) -> str:
    """Нормализовать цель: @user / t.me/user / numeric id → канон для resolve."""
    text = str(raw or "").replace("\u00a0", " ").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)^\s*id\s*:\s*", "", text).strip()
    m = _TME_RE.search(text)
    if m:
        return f"@{m.group(1)}"
    if text.startswith("@"):
        handle = re.sub(r"[^A-Za-z0-9_]", "", text[1:])
        return f"@{handle}" if handle else ""
    # Чистый username без @
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", text):
        return f"@{text}"
    digits = re.sub(r"\D", "", text)
    if digits and (digits == re.sub(r"[^\d\-]", "", text).lstrip("-") or " " in text or text.isdigit() or text.lstrip("-").isdigit()):
        # отрицательный peer целиком
        if text.strip().startswith("-") and text.strip()[1:].isdigit():
            return text.strip()
        return digits
    return text


def parse_group_targets(raw: str | None) -> list[str]:
    """Список целей: numeric id и/или @username / t.me/… через запятую."""
    text = str(raw or "").replace("\u00a0", " ").strip()
    if not text:
        return []
    found: list[str] = []
    for chunk in re.split(r"[,;]+", text):
        # «id: 1 701 957 243» внутри chunk
        chunk_s = chunk.strip()
        if not chunk_s:
            continue
        if re.search(r"(?i)\bid\s*:", chunk_s) or (
            re.search(r"\d", chunk_s) and " " in chunk_s and "@" not in chunk_s and "t.me" not in chunk_s.lower()
        ):
            digits = re.sub(r"\D", "", re.sub(r"(?i)\bid\s*:", " ", chunk_s))
            if digits:
                found.append(digits)
                continue
        norm = normalize_group_target(chunk_s)
        if norm:
            found.append(norm)
    seen: set[str] = set()
    unique: list[str] = []
    for item in found:
        key = item.lower() if item.startswith("@") else item
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def parse_group_ids(raw: str | None) -> list[str]:
    """Обратная совместимость: только числовые id из строки."""
    return [t for t in parse_group_targets(raw) if t.lstrip("-").isdigit()]


def to_telethon_peer_id(group_id: str | int) -> int:
    """Положительный channel/megagroup id → peer id -100…; отрицательный — как есть."""
    raw = int(str(group_id).strip())
    if raw < 0:
        return raw
    as_str = str(raw)
    if as_str.startswith("100") and len(as_str) > 10:
        return -int(as_str)
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
    "normalize_group_target",
    "parse_group_ids",
    "parse_group_targets",
    "to_telethon_peer_id",
    "utc_now_iso",
    "validate_user_messages_headers",
]
