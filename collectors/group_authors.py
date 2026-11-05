"""Сбор авторов сообщений из обычных Telegram-групп (не полный roster)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat, User

from config.settings import config
from utils.group_authors_contract import (
    MESSAGE_TYPE_GROUP_AUTHOR,
    make_author_id,
    to_telethon_peer_id,
    utc_now_iso,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GroupAuthorsCollectStats:
    groups_scanned: int = 0
    groups_ok: int = 0
    messages_scanned: int = 0
    authors_collected: int = 0
    errors: int = 0
    skipped_broadcast: int = 0
    skipped_no_access: int = 0


def _sender_fields(sender: Any) -> tuple[str, str, bool]:
    """user_id, user_name, is_bot."""
    if sender is None:
        return "", "", False
    uid = getattr(sender, "id", None)
    username = getattr(sender, "username", None) or ""
    if not username:
        first = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        username = (first + " " + last).strip()
    if not username:
        username = str(getattr(sender, "title", None) or "").strip()
    is_bot = bool(getattr(sender, "bot", False)) if (
        isinstance(sender, User) or hasattr(sender, "bot")
    ) else False
    return (str(uid) if uid is not None else "", str(username).strip(), is_bot)


def _is_broadcast_channel(entity: Any) -> bool:
    if bool(getattr(entity, "megagroup", False)):
        return False
    if isinstance(entity, Channel) or hasattr(entity, "broadcast"):
        return bool(getattr(entity, "broadcast", False))
    return False


def _is_group_chat(entity: Any) -> bool:
    if isinstance(entity, Chat):
        return True
    if isinstance(entity, Channel):
        return not _is_broadcast_channel(entity)
    # duck-typing (тесты / нестандартные entity)
    if bool(getattr(entity, "megagroup", False)):
        return True
    title = getattr(entity, "title", None)
    return bool(title) and not bool(getattr(entity, "broadcast", False))


async def _sleep_flood(exc: FloodWaitError, *, attempt: int) -> bool:
    wait_s = min(int(getattr(exc, "seconds", 5) or 5), 120)
    LOGGER.warning("group_authors FloodWait %ss (attempt %s)", wait_s, attempt)
    await asyncio.sleep(wait_s)
    return attempt < 3


async def collect_group_message_authors(
    client: Any,
    *,
    group_id: str,
    group_title: str = "",
    message_limit: int | None = None,
    delay_seconds: float | None = None,
    skip_bots: bool | None = None,
    min_message_id: int = 0,
    source_name: str = "",
) -> tuple[list[dict[str, Any]], GroupAuthorsCollectStats, int]:
    """Собрать уникальных авторов из последних сообщений группы.

    Returns: (records, stats, max_message_id_seen)
    """
    stats = GroupAuthorsCollectStats(groups_scanned=1)
    limit = max(1, int(message_limit or getattr(config, "GROUP_AUTHORS_MESSAGE_LIMIT", 100)))
    delay = float(
        delay_seconds
        if delay_seconds is not None
        else getattr(config, "GROUP_AUTHORS_DELAY_SECONDS", 0.1)
    )
    if skip_bots is None:
        skip_bots = bool(getattr(config, "GROUP_AUTHORS_SKIP_BOTS", True))
    out_by_user: dict[str, dict[str, Any]] = {}
    max_msg_id = int(min_message_id or 0)

    peer = to_telethon_peer_id(group_id)
    try:
        entity = await client.get_entity(peer)
    except Exception:
        try:
            entity = await client.get_entity(int(group_id))
        except Exception:
            stats.errors += 1
            stats.skipped_no_access += 1
            LOGGER.exception("group_authors: cannot resolve group_id=%s peer=%s", group_id, peer)
            return [], stats, max_msg_id

    if _is_broadcast_channel(entity):
        stats.skipped_broadcast += 1
        LOGGER.info("group_authors: skip broadcast channel id=%s (use /collect_commenters)", group_id)
        return [], stats, max_msg_id

    if not _is_group_chat(entity):
        stats.errors += 1
        LOGGER.warning("group_authors: entity is not a chat/group id=%s type=%s", group_id, type(entity))
        return [], stats, max_msg_id

    title = (
        group_title
        or getattr(entity, "title", None)
        or source_name
        or str(group_id)
    )
    canonical_group_id = str(group_id).strip()
    attempt = 0
    try:
        while True:
            try:
                async for msg in client.iter_messages(
                    entity,
                    limit=limit,
                    min_id=max(0, int(min_message_id or 0)),
                ):
                    if msg is None or not getattr(msg, "id", None):
                        continue
                    mid = int(msg.id)
                    if mid > max_msg_id:
                        max_msg_id = mid
                    stats.messages_scanned += 1
                    if getattr(msg, "out", False):
                        continue
                    # service / empty without sender
                    sender = getattr(msg, "sender", None)
                    if sender is None and hasattr(msg, "get_sender"):
                        try:
                            sender = await msg.get_sender()
                        except Exception:
                            sender = None
                    user_id, user_name, is_bot = _sender_fields(sender)
                    if skip_bots and is_bot:
                        continue
                    if not user_id and not user_name:
                        continue
                    key = user_id or user_name
                    author_id = make_author_id(group_id=canonical_group_id, user_id=key)
                    snippet = str(getattr(msg, "message", None) or "").strip()
                    msg_at = ""
                    if getattr(msg, "date", None):
                        msg_at = msg.date.replace(tzinfo=timezone.utc).isoformat()
                    existing = out_by_user.get(author_id)
                    if existing is None:
                        out_by_user[author_id] = {
                            "author_id": author_id,
                            "group_id": canonical_group_id,
                            "group_title": title,
                            "user_id": user_id,
                            "user_name": user_name,
                            "last_message_id": str(mid),
                            "last_message_at": msg_at or utc_now_iso(),
                            "message_snippet": snippet[:500],
                            "messages_seen": 1,
                            "source_name": source_name or title,
                            "message_type": MESSAGE_TYPE_GROUP_AUTHOR,
                            # sheet lookup
                            "message_id": author_id,
                            "related_id": canonical_group_id,
                            "comment_text": snippet[:500],
                            "comment_at": msg_at or utc_now_iso(),
                        }
                    else:
                        existing["messages_seen"] = int(existing.get("messages_seen") or 1) + 1
                        # iter_messages идёт от новых к старым — first wins as last_message
                    if delay > 0:
                        await asyncio.sleep(delay)
                break
            except FloodWaitError as fw:
                attempt += 1
                if not await _sleep_flood(fw, attempt=attempt):
                    stats.errors += 1
                    break
    except Exception:
        stats.errors += 1
        LOGGER.exception("group_authors: iter_messages failed group_id=%s", group_id)

    records = list(out_by_user.values())
    stats.authors_collected = len(records)
    if stats.errors == 0 and stats.skipped_broadcast == 0 and stats.skipped_no_access == 0:
        stats.groups_ok = 1
    LOGGER.info(
        "group_authors group_id=%s messages=%s authors=%s",
        canonical_group_id,
        stats.messages_scanned,
        stats.authors_collected,
    )
    return records, stats, max_msg_id


async def collect_groups_authors(
    client: Any,
    group_ids: list[str],
    *,
    message_limit: int | None = None,
    delay_seconds: float | None = None,
    skip_bots: bool | None = None,
    checkpoints: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], GroupAuthorsCollectStats, dict[str, int]]:
    """Сбор по списку group id. checkpoints: group_id → last_message_id."""
    total = GroupAuthorsCollectStats()
    merged: list[dict[str, Any]] = []
    new_checkpoints: dict[str, int] = {}
    cps = checkpoints or {}
    pause = float(getattr(config, "MESSAGE_DELAY", 2) or 2)
    for gid in group_ids:
        gid_s = str(gid).strip()
        if not gid_s:
            continue
        rows, st, max_mid = await collect_group_message_authors(
            client,
            group_id=gid_s,
            message_limit=message_limit,
            delay_seconds=delay_seconds,
            skip_bots=skip_bots,
            min_message_id=int(cps.get(gid_s) or 0),
        )
        merged.extend(rows)
        if max_mid > 0:
            new_checkpoints[gid_s] = max_mid
        total.groups_scanned += st.groups_scanned
        total.groups_ok += st.groups_ok
        total.messages_scanned += st.messages_scanned
        total.authors_collected += st.authors_collected
        total.errors += st.errors
        total.skipped_broadcast += st.skipped_broadcast
        total.skipped_no_access += st.skipped_no_access
        await asyncio.sleep(pause)
    return merged, total, new_checkpoints


__all__ = [
    "GroupAuthorsCollectStats",
    "collect_group_message_authors",
    "collect_groups_authors",
]
