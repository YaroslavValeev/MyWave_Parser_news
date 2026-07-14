"""Парсинг iCal/webcal календарей соревнований (WWA Tribe Events и др.)."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import requests

LOGGER = logging.getLogger(__name__)

_VEVENT_RE = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL | re.IGNORECASE)
_UNFOLD_RE = re.compile(r"\r?\n[ \t]")
_ICAL_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})")


def ical_url_to_https(url: str) -> str:
    """webcal:// → https:// для HTTP-загрузки."""
    text = str(url or "").strip()
    if text.lower().startswith("webcal://"):
        return "https://" + text[9:]
    return text


def _unfold_ical(text: str) -> str:
    return _UNFOLD_RE.sub("", text)


def _parse_ical_date(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    m = _ICAL_DATE_RE.match(raw[:8])
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _field(block: str, name: str) -> str:
    pattern = re.compile(rf"^{name}[;:](.+)$", re.MULTILINE | re.IGNORECASE)
    m = pattern.search(block)
    if not m:
        return ""
    val = m.group(1).strip()
    if ":" in val.split(";")[0] and name.upper() == "DTSTART":
        # DTSTART;VALUE=DATE:20260626
        parts = val.split(":")
        if len(parts) >= 2 and parts[-1][:8].isdigit():
            return parts[-1]
    if ":" in val and not val[:8].isdigit():
        val = val.split(":")[-1]
    return unquote(val.replace("\\n", " ").replace("\\,", ",").strip())


def _make_event_id(source: str, title: str, start: str) -> str:
    base = f"{source}|{title}|{start}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def parse_ical_events(
    ical_text: str,
    *,
    source_name: str,
    discipline: str = "both",
    source_url: str = "",
) -> list[dict[str, Any]]:
    """Разбор текста .ics → строки competitions_ticker."""
    today = datetime.now(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    body = _unfold_ical(ical_text or "")

    for match in _VEVENT_RE.finditer(body):
        block = match.group(1)
        title = _field(block, "SUMMARY")
        if not title or len(title) < 3:
            continue
        start = _parse_ical_date(_field(block, "DTSTART"))
        if not start:
            continue
        try:
            if date.fromisoformat(start) < today:
                continue
        except ValueError:
            continue
        end = _parse_ical_date(_field(block, "DTEND")) or start
        try:
            if date.fromisoformat(end) < date.fromisoformat(start):
                end = start
        except ValueError:
            end = start
        href = _field(block, "URL")
        location = _field(block, "LOCATION")
        eid = _make_event_id(source_name, title, start)
        if eid in seen:
            continue
        seen.add(eid)
        rows.append(
            {
                "id": f"{source_name}-{eid}",
                "status": "ACTIVE",
                "discipline": discipline,
                "event_name": title[:200],
                "location": location[:120] if location else "",
                "country": "",
                "start_date": start,
                "end_date": end,
                "event_url": href or source_url,
                "source_name": source_name.upper() if source_name == "wwa" else source_name,
                "source_url": source_url or href or "",
            }
        )
        if len(rows) >= 50:
            break

    LOGGER.info("competitions_ical_calendar source=%s events=%s", source_name, len(rows))
    return rows


def fetch_ical_calendar_events(
    calendar_url: str,
    *,
    source_name: str,
    discipline: str = "both",
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Загрузить .ics и вернуть события."""
    fetch_url = ical_url_to_https(calendar_url)
    if not fetch_url:
        return []
    try:
        resp = requests.get(
            fetch_url,
            timeout=timeout,
            headers={"User-Agent": "MyWaveParserNews/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning(
            "competitions ical fetch failed url=%s err=%s",
            fetch_url[:80],
            exc,
        )
        return []
    return parse_ical_events(
        resp.text,
        source_name=source_name,
        discipline=discipline,
        source_url=fetch_url,
    )


__all__ = [
    "fetch_ical_calendar_events",
    "ical_url_to_https",
    "parse_ical_events",
]
