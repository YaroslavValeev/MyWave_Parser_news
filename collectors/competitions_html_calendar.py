"""Парсинг HTML-страниц календарей соревнований (IWWF, WSWS и др.)."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")


def _parse_date(text: str) -> str | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _make_event_id(source: str, title: str, start: str) -> str:
    base = f"{source}|{title}|{start}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def fetch_calendar_events(
    page_url: str,
    *,
    source_name: str,
    discipline: str = "both",
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Извлечь события из HTML (эвристика: заголовки + даты на странице)."""
    if not page_url:
        return []
    try:
        resp = requests.get(
            page_url,
            timeout=timeout,
            headers={"User-Agent": "MyWaveParserNews/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("competitions calendar fetch failed url=%s err=%s", page_url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    today = datetime.now(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tag in soup.find_all(["h2", "h3", "h4", "article", "li", "div"]):
        text = tag.get_text(" ", strip=True)
        if len(text) < 12 or len(text) > 300:
            continue
        start = _parse_date(text)
        if not start:
            continue
        try:
            if date.fromisoformat(start) < today:
                continue
        except ValueError:
            continue
        title = text[:200]
        eid = _make_event_id(source_name, title, start)
        if eid in seen:
            continue
        seen.add(eid)
        end = start
        href = ""
        a = tag.find("a", href=True)
        if a:
            href = urljoin(page_url, a["href"])
        rows.append(
            {
                "id": f"{source_name}-{eid}",
                "status": "ACTIVE",
                "discipline": discipline,
                "event_name": title,
                "location": "",
                "country": "",
                "start_date": start,
                "end_date": end,
                "event_url": href or page_url,
                "source_name": source_name,
                "source_url": page_url,
            }
        )
        if len(rows) >= 30:
            break
    LOGGER.info("competitions_html_calendar source=%s events=%s", source_name, len(rows))
    return rows


__all__ = ["fetch_calendar_events"]
