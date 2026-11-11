"""Контракт group_members + маппинг в лист user_messages."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from utils.group_authors_contract import (
    STATUS_COLLECTED,
    USER_MESSAGES_COLUMNS,
    USER_MESSAGES_SHEET_NAME,
    normalize_group_target,
    parse_group_targets,
    to_telethon_peer_id,
    utc_now_iso,
    validate_user_messages_headers,
)

MESSAGE_TYPE_GROUP_MEMBER = "group_member"


def make_member_id(*, group_id: str, user_id: str) -> str:
    base = f"member|{str(group_id).strip()}|{str(user_id).strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_user_messages_row(record: Mapping[str, Any]) -> dict[str, str]:
    """Строка user_messages: 1 участник × 1 группа."""
    user_name = str(record.get("user_name") or "").strip()
    if not user_name and record.get("user_id"):
        user_name = str(record.get("user_id"))
    message_id = str(record.get("member_id") or record.get("message_id") or "").strip()
    related = str(record.get("group_id") or record.get("related_id") or "").strip()
    flags: list[str] = []
    if record.get("is_admin"):
        flags.append("admin")
    if record.get("is_bot"):
        flags.append("bot")
    text = ", ".join(flags) if flags else "member"
    return {
        "message_id": message_id,
        "user_id": str(record.get("user_id") or ""),
        "user_name": user_name,
        "related_id": related,
        "text": text,
        "message_type": str(record.get("message_type") or MESSAGE_TYPE_GROUP_MEMBER),
        "timestamp": str(record.get("timestamp") or record.get("updated_at") or utc_now_iso()),
        "status": str(record.get("status") or STATUS_COLLECTED),
    }


__all__ = [
    "MESSAGE_TYPE_GROUP_MEMBER",
    "STATUS_COLLECTED",
    "USER_MESSAGES_COLUMNS",
    "USER_MESSAGES_SHEET_NAME",
    "build_user_messages_row",
    "make_member_id",
    "normalize_group_target",
    "parse_group_targets",
    "to_telethon_peer_id",
    "utc_now_iso",
    "validate_user_messages_headers",
]
