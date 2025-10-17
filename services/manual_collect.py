"""Utilities for on-demand parsing of individual sources."""
from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Tuple
from urllib.parse import urlparse

from config.settings import config
from storage.data import save_contacts, save_news

LOGGER = logging.getLogger(__name__)

DEFAULT_LIMIT = 50


@dataclass(slots=True)
class ManualCollectResult:
    """Summary of a manual collection run."""

    total: int
    saved: int
    filtered_out: int
    source_type: str
    since: datetime | None
    contacts_saved: int = 0


@dataclass(slots=True)
class ManualSource:
    type: str
    url: str
    name: str


async def collect_single_source(
    url: str,
    *,
    source_type: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> ManualCollectResult:
    """Collect one source immediately and persist fresh items."""

    normalized_url = url.strip()
    if not normalized_url:
        raise ValueError("URL для парсинга не может быть пустым")

    resolved_type = (source_type or guess_source_type(normalized_url)).lower()
    source = ManualSource(type=resolved_type, url=normalized_url, name=normalized_url)

    items, contacts = await _fetch_items(source, limit=limit)
    total = len(items)

    cutoff = since.astimezone(timezone.utc) if since else None
    if cutoff:
        filtered = [item for item in items if _item_is_newer_or_unknown(item, cutoff)]
        contact_candidates = [
            contact for contact in contacts if _contact_is_newer_or_unknown(contact, cutoff)
        ]
    else:
        filtered = items
        contact_candidates = contacts

    saved = await save_news(filtered)
    contacts_saved = await save_contacts(contact_candidates) if contact_candidates else 0
    LOGGER.info(
        "Manual collect finished: url=%s type=%s total=%s saved=%s filtered_out=%s contacts_saved=%s",
        normalized_url,
        resolved_type,
        total,
        saved,
        total - len(filtered),
        contacts_saved,
    )
    return ManualCollectResult(
        total=total,
        saved=saved,
        filtered_out=total - len(filtered),
        source_type=resolved_type,
        since=cutoff,
        contacts_saved=contacts_saved,
    )


def guess_source_type(url: str) -> str:
    """Heuristically determine source type from URL."""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.lower()

    if "t.me" in host or host.endswith("telegram.me"):
        return "telegram"
    if "youtube.com" in host or host.endswith("youtu.be"):
        return "youtube"
    if path.endswith(".xml") or "rss" in host or "feed" in path:
        return "rss"
    return "website"


async def _fetch_items(source: ManualSource, limit: int | None = None) -> Tuple[list[dict], list[dict]]:
    if source.type == "telegram":
        return await _fetch_telegram_items(source, limit)
    if source.type == "rss":
        parser = _load_rss_parser()
        return ([_convert_raw_entry(entry, source) for entry in parser(source, [])], [])
    if source.type == "youtube":
        parser = _load_youtube_parser()
        return ([_convert_raw_entry(entry, source) for entry in parser(source, [])], [])
    if source.type == "website":
        fetcher = _load_website_parser()
        raw_entries = await fetcher(source, [])
        return ([_convert_raw_entry(entry, source) for entry in raw_entries], [])
    raise ValueError(f"Неизвестный тип источника: {source.type}")


async def _fetch_telegram_items(source: ManualSource, limit: int | None) -> Tuple[list[dict], list[dict]]:
    from utils.telegram_session import TelegramSessionManager

    session_manager = TelegramSessionManager(
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE,
    )
    client = await session_manager.get_client()
    if client is None:
        raise RuntimeError("Не удалось инициализировать TelegramClient")

    parser_cls = _load_telethon_parser()
    parser = parser_cls(limit=limit or config.MAX_MESSAGES or DEFAULT_LIMIT)
    contacts_parser_cls = _load_contacts_parser()
    contacts_parser = contacts_parser_cls(client)
    items: list[dict] = []
    async for raw in parser.parse(client, source):  # type: ignore[arg-type]
        items.append(_convert_raw_entry(raw, source))
    contacts = await contacts_parser.parse_contacts(source)
    await session_manager.close_client()
    return items, contacts


def _convert_raw_entry(entry: dict, source: ManualSource) -> dict:
    """Transform collector payload into storage format."""

    raw_media = entry.get("raw_media")
    media_urls: list[str] = []
    if isinstance(raw_media, str) and raw_media:
        try:
            decoded = json.loads(raw_media)
            if isinstance(decoded, list):
                media_urls = [str(item) for item in decoded if item]
        except json.JSONDecodeError:
            media_urls = [raw_media]
    elif isinstance(raw_media, Iterable):
        media_urls = [str(item) for item in raw_media if item]

    date_str = _normalize_datetime(entry.get("created_at") or entry.get("date"))

    return {
        "source": entry.get("source_name") or source.name,
        "title": entry.get("raw_title") or entry.get("title") or "(без заголовка)",
        "content": entry.get("raw_content") or entry.get("content") or "",
        "link": _resolve_link(entry, source),
        "date": date_str,
        "images": "\n".join(media_urls) if media_urls else None,
        "videos": None,
        "transcript": entry.get("transcript"),
        "comment": entry.get("comment"),
    }


def _normalize_datetime(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[: len(fmt)], fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def _item_is_newer_or_unknown(item: dict, cutoff: datetime) -> bool:
    raw = item.get("date")
    if raw is None:
        return True
    parsed = _parse_datetime(raw)
    if parsed is None:
        return True
    return parsed >= cutoff


def _contact_is_newer_or_unknown(contact: dict, cutoff: datetime) -> bool:
    raw = contact.get("date_found")
    if raw is None:
        return True
    parsed = _parse_datetime(raw)
    if parsed is None:
        return True
    return parsed >= cutoff


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value is None:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[: len(fmt)], fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_link(entry: dict, source: ManualSource) -> str | None:
    direct = entry.get("link")
    if isinstance(direct, str) and direct:
        return direct
    debug_info = entry.get("debug_info")
    if isinstance(debug_info, str) and debug_info:
        for prefix in ("rss_link=", "yt_link=", "site_link="):
            if debug_info.startswith(prefix):
                return debug_info[len(prefix) :]
    identifier = entry.get("id")
    if source.type == "telegram" and identifier:
        return f"{source.url.rstrip('/')}/{identifier}"
    return None


def _load_rss_parser():
    from collectors import rss_parser

    return rss_parser.parse_rss


def _load_youtube_parser():
    from collectors import youtube_parser

    return youtube_parser.parse_youtube


def _load_website_parser():
    from collectors import website_parser

    return website_parser.parse_website_async


def _load_telethon_parser():
    from collectors.telegram_parser import TelethonParser

    return TelethonParser


def _load_contacts_parser():
    from collectors.contacts_parser import ContactsParser

    return ContactsParser


def parse_period_argument(arg: str) -> datetime:
    """Convert relative period specification into cutoff datetime."""

    now = datetime.now(timezone.utc)
    chunks = [chunk for chunk in shlex.split(arg) if chunk]
    if not chunks:
        raise ValueError("Период не указан")
    delta = timedelta()
    for chunk in chunks:
        number_part = "".join(ch for ch in chunk if ch.isdigit())
        unit_part = chunk[len(number_part) :].lower()
        if not number_part or unit_part not in {"d", "h", "m"}:
            raise ValueError("Формат периода: <число>[d|h|m]")
        value = int(number_part)
        if unit_part == "d":
            delta += timedelta(days=value)
        elif unit_part == "h":
            delta += timedelta(hours=value)
        elif unit_part == "m":
            delta += timedelta(minutes=value)
    if delta <= timedelta(0):
        raise ValueError("Период должен быть положительным")
    return now - delta


__all__ = [
    "collect_single_source",
    "guess_source_type",
    "parse_period_argument",
    "ManualCollectResult",
]
