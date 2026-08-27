"""Сбор комментариев под постами Telegram-каналов (linked discussion)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping

from telethon.errors import FloodWaitError, MsgIdInvalidError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetDiscussionMessageRequest
from telethon.tl.types import Channel

from config.settings import config
from utils.channel_commenters_contract import make_commenter_id, normalize_channel_url, utc_now_iso

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EngagementCollectStats:
    channels_scanned: int = 0
    channels_with_discussion: int = 0
    posts_scanned: int = 0
    comments_collected: int = 0
    errors: int = 0
    skipped_no_discussion: int = 0


def _sender_fields(sender: Any) -> tuple[str, str]:
    if sender is None:
        return "", ""
    uid = getattr(sender, "id", None)
    username = getattr(sender, "username", None) or ""
    if not username:
        first = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        username = (first + " " + last).strip()
    return (str(uid) if uid is not None else "", str(username).strip())


async def _iter_post_comments(
    client: Any,
    *,
    channel_entity: Any,
    discussion_entity: Any,
    post_id: int,
    limit: int,
) -> AsyncIterator[Any]:
    """Комментарии к посту: discussion-группа (канон) или reply_to в канале."""
    if discussion_entity is not channel_entity:
        try:
            disc = await client(
                GetDiscussionMessageRequest(peer=channel_entity, msg_id=post_id)
            )
            if disc.messages:
                root_id = disc.messages[0].id
                async for comment in client.iter_messages(
                    discussion_entity,
                    reply_to=root_id,
                    limit=limit,
                ):
                    yield comment
                return
        except MsgIdInvalidError:
            pass
        except Exception:
            LOGGER.debug(
                "engagement: GetDiscussionMessage failed post=%s",
                post_id,
                exc_info=True,
            )
    async for comment in client.iter_messages(
        channel_entity,
        reply_to=post_id,
        limit=limit,
    ):
        yield comment


async def _sleep_flood(exc: FloodWaitError, *, attempt: int) -> bool:
    wait_s = min(int(getattr(exc, "seconds", 5) or 5), 120)
    LOGGER.warning("Telegram FloodWait %ss (attempt %s)", wait_s, attempt)
    await asyncio.sleep(wait_s)
    return attempt < 3


async def collect_channel_comments(
    client: Any,
    *,
    channel_url: str,
    channel_title: str = "",
    posts_limit: int | None = None,
    comments_per_post: int | None = None,
    source_name: str = "",
) -> tuple[list[dict[str, Any]], EngagementCollectStats]:
    """Собрать комментарии для одного канала."""
    stats = EngagementCollectStats(channels_scanned=1)
    url = normalize_channel_url(channel_url)
    posts_limit = max(1, int(posts_limit or getattr(config, "ENGAGEMENT_POSTS_LIMIT", 15)))
    comments_per_post = max(
        1, int(comments_per_post or getattr(config, "ENGAGEMENT_COMMENTS_PER_POST", 50))
    )
    out: list[dict[str, Any]] = []

    try:
        entity = await client.get_entity(url)
    except Exception:
        stats.errors += 1
        LOGGER.exception("engagement: cannot resolve channel %s", url)
        return out, stats

    discussion = entity
    if isinstance(entity, Channel):
        try:
            full = await client(GetFullChannelRequest(channel=entity))
            linked_id = getattr(full.full_chat, "linked_chat_id", None)
            if linked_id:
                discussion = await client.get_entity(linked_id)
                stats.channels_with_discussion = 1
            else:
                stats.skipped_no_discussion = 1
                LOGGER.info("engagement: no linked discussion for %s", url)
                return out, stats
        except Exception:
            stats.errors += 1
            LOGGER.exception("engagement: GetFullChannel failed %s", url)
            return out, stats
    else:
        stats.channels_with_discussion = 1

    title = channel_title or getattr(entity, "title", None) or url
    posts_seen = 0

    try:
        async for post in client.iter_messages(entity, limit=posts_limit):
            if post is None or not getattr(post, "id", None):
                continue
            replies = getattr(post, "replies", None)
            if not replies or not getattr(replies, "replies", 0):
                continue
            posts_seen += 1
            post_id = str(post.id)
            attempt = 0
            while True:
                try:
                    async for comment in _iter_post_comments(
                        client,
                        channel_entity=entity,
                        discussion_entity=discussion,
                        post_id=post.id,
                        limit=comments_per_post,
                    ):
                        if comment is None or not getattr(comment, "id", None):
                            continue
                        if getattr(comment, "out", False):
                            continue
                        user_id, user_name = _sender_fields(getattr(comment, "sender", None))
                        if not user_id and not user_name:
                            continue
                        msg_id = str(comment.id)
                        comment_at = ""
                        if getattr(comment, "date", None):
                            comment_at = comment.date.replace(tzinfo=timezone.utc).isoformat()
                        record = {
                            "commenter_id": make_commenter_id(
                                channel_url=url, message_id=msg_id
                            ),
                            "channel_url": url,
                            "channel_title": title,
                            "post_id": post_id,
                            "message_id": msg_id,
                            "user_id": user_id,
                            "user_name": user_name,
                            "comment_text": str(getattr(comment, "message", None) or ""),
                            "comment_at": comment_at or utc_now_iso(),
                            "source_name": source_name or title,
                        }
                        out.append(record)
                    break
                except FloodWaitError as fw:
                    attempt += 1
                    if not await _sleep_flood(fw, attempt=attempt):
                        stats.errors += 1
                        break
                except Exception:
                    stats.errors += 1
                    LOGGER.debug(
                        "engagement: comments failed channel=%s post=%s",
                        url,
                        post_id,
                        exc_info=True,
                    )
                    break
    except FloodWaitError as fw:
        await _sleep_flood(fw, attempt=1)
        stats.errors += 1
    except Exception:
        stats.errors += 1
        LOGGER.exception("engagement: iter_messages failed %s", url)

    stats.posts_scanned = posts_seen
    stats.comments_collected = len(out)
    LOGGER.info(
        "engagement channel=%s posts=%s comments=%s",
        url,
        posts_seen,
        len(out),
    )
    return out, stats


async def collect_channels_engagement(
    client: Any,
    sources: list[Mapping[str, Any]],
    *,
    posts_limit: int | None = None,
    comments_per_post: int | None = None,
) -> tuple[list[dict[str, Any]], EngagementCollectStats]:
    """Сбор по списку telegram-источников."""
    total = EngagementCollectStats()
    merged: list[dict[str, Any]] = []
    for src in sources:
        url = str(src.get("url") or src.get("source_url") or "").strip()
        if not url:
            continue
        name = str(src.get("name") or src.get("source_name") or url)
        rows, st = await collect_channel_comments(
            client,
            channel_url=url,
            channel_title=name,
            posts_limit=posts_limit,
            comments_per_post=comments_per_post,
            source_name=name,
        )
        merged.extend(rows)
        total.channels_scanned += st.channels_scanned
        total.channels_with_discussion += st.channels_with_discussion
        total.posts_scanned += st.posts_scanned
        total.comments_collected += st.comments_collected
        total.errors += st.errors
        total.skipped_no_discussion += st.skipped_no_discussion
        await asyncio.sleep(float(getattr(config, "MESSAGE_DELAY", 2)))
    return merged, total


__all__ = [
    "EngagementCollectStats",
    "collect_channel_comments",
    "collect_channels_engagement",
]
