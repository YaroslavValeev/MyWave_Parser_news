"""Сбор видимых участников Telegram-групп (не подписчиков broadcast-канала)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from telethon.errors import ChatAdminRequiredError, FloodWaitError
from telethon.tl.types import Channel, Chat, ChannelParticipantsAdmins, User

from collectors.group_authors import (
    _is_broadcast_channel,
    _is_group_chat,
    _linked_discussion,
    _resolve_entity,
    _sender_fields,
)
from config.settings import config
from utils.group_members_contract import (
    MESSAGE_TYPE_GROUP_MEMBER,
    make_member_id,
    normalize_group_target,
    utc_now_iso,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GroupMembersCollectStats:
    groups_scanned: int = 0
    groups_ok: int = 0
    members_collected: int = 0
    errors: int = 0
    skipped_broadcast: int = 0
    skipped_admin_required: int = 0
    skipped_no_access: int = 0


async def _sleep_flood(exc: FloodWaitError, *, attempt: int) -> bool:
    wait_s = min(int(getattr(exc, "seconds", 5) or 5), 120)
    LOGGER.warning("group_members FloodWait %ss (attempt %s)", wait_s, attempt)
    await asyncio.sleep(wait_s)
    return attempt < 3


async def collect_group_members(
    client: Any,
    *,
    group_id: str,
    group_title: str = "",
    member_limit: int | None = None,
    delay_seconds: float | None = None,
    skip_bots: bool | None = None,
    source_name: str = "",
) -> tuple[list[dict[str, Any]], GroupMembersCollectStats]:
    """Собрать видимых участников группы / megagroup.

    Для broadcast-канала клуба — берём roster linked discussion (не подписчиков канала).
    Канал без discussion пропускается.
    """
    stats = GroupMembersCollectStats(groups_scanned=1)
    limit = max(1, int(member_limit or getattr(config, "GROUP_MEMBERS_LIMIT", 500)))
    delay = float(
        delay_seconds
        if delay_seconds is not None
        else getattr(config, "GROUP_MEMBERS_DELAY_SECONDS", 0.05)
    )
    if skip_bots is None:
        skip_bots = bool(getattr(config, "GROUP_MEMBERS_SKIP_BOTS", True))

    entity = await _resolve_entity(client, group_id)
    if entity is None:
        stats.errors += 1
        stats.skipped_no_access += 1
        return [], stats

    scan_entity = entity
    if _is_broadcast_channel(entity):
        linked = await _linked_discussion(client, entity)
        if linked is None:
            stats.skipped_broadcast += 1
            LOGGER.info(
                "group_members: broadcast without discussion target=%s "
                "(subscribers hidden)",
                group_id,
            )
            return [], stats
        scan_entity = linked
        LOGGER.info(
            "group_members: using linked discussion for channel target=%s",
            group_id,
        )

    if not _is_group_chat(scan_entity) and not isinstance(scan_entity, (Chat, Channel)):
        stats.errors += 1
        LOGGER.warning(
            "group_members: not a group target=%s type=%s",
            group_id,
            type(scan_entity),
        )
        return [], stats

    title = (
        group_title
        or getattr(entity, "title", None)
        or source_name
        or str(group_id)
    )
    uname = getattr(entity, "username", None)
    if uname:
        canonical_group_id = f"@{uname}"
    else:
        eid = getattr(entity, "id", None)
        canonical_group_id = (
            str(eid) if eid is not None else normalize_group_target(group_id) or str(group_id).strip()
        )

    admin_ids: set[str] = set()
    try:
        async for admin in client.iter_participants(
            scan_entity, filter=ChannelParticipantsAdmins()
        ):
            uid = getattr(admin, "id", None)
            if uid is not None:
                admin_ids.add(str(uid))
    except ChatAdminRequiredError:
        LOGGER.debug("group_members: admin list unavailable target=%s", group_id)
    except FloodWaitError as exc:
        await _sleep_flood(exc, attempt=1)
    except Exception:
        LOGGER.debug("group_members: admin list failed target=%s", group_id, exc_info=True)

    out: dict[str, dict[str, Any]] = {}
    attempt = 0
    try:
        while True:
            try:
                async for user in client.iter_participants(scan_entity, limit=limit):
                    uid, uname_disp, is_bot = _sender_fields(user)
                    if not uid:
                        continue
                    if skip_bots and is_bot:
                        continue
                    if not isinstance(user, User) and not hasattr(user, "id"):
                        continue
                    mid = make_member_id(group_id=canonical_group_id, user_id=uid)
                    out[uid] = {
                        "member_id": mid,
                        "group_id": canonical_group_id,
                        "group_title": title,
                        "user_id": uid,
                        "user_name": uname_disp,
                        "is_bot": 1 if is_bot else 0,
                        "is_admin": 1 if uid in admin_ids else 0,
                        "source_name": source_name or title,
                        "message_type": MESSAGE_TYPE_GROUP_MEMBER,
                        "timestamp": utc_now_iso(),
                    }
                    if delay > 0:
                        await asyncio.sleep(delay)
                break
            except ChatAdminRequiredError:
                stats.skipped_admin_required += 1
                LOGGER.info(
                    "group_members: ChatAdminRequired target=%s (roster hidden)",
                    group_id,
                )
                return [], stats
            except FloodWaitError as exc:
                attempt += 1
                if not await _sleep_flood(exc, attempt=attempt):
                    stats.errors += 1
                    raise
            except Exception:
                stats.errors += 1
                LOGGER.exception("group_members: iter_participants failed group_id=%s", group_id)
                return [], stats
    except FloodWaitError:
        return [], stats

    rows = list(out.values())
    stats.members_collected = len(rows)
    if rows:
        stats.groups_ok = 1
    LOGGER.info(
        "group_members group_id=%s members=%s",
        canonical_group_id,
        len(rows),
    )
    return rows, stats


async def collect_groups_members(
    client: Any,
    targets: list[str],
    *,
    member_limit: int | None = None,
) -> tuple[list[dict[str, Any]], GroupMembersCollectStats]:
    total = GroupMembersCollectStats()
    all_rows: list[dict[str, Any]] = []
    for target in targets:
        rows, st = await collect_group_members(
            client,
            group_id=target,
            member_limit=member_limit,
        )
        all_rows.extend(rows)
        total.groups_scanned += st.groups_scanned
        total.groups_ok += st.groups_ok
        total.members_collected += st.members_collected
        total.errors += st.errors
        total.skipped_broadcast += st.skipped_broadcast
        total.skipped_admin_required += st.skipped_admin_required
        total.skipped_no_access += st.skipped_no_access
    return all_rows, total


__all__ = [
    "GroupMembersCollectStats",
    "collect_group_members",
    "collect_groups_members",
]
