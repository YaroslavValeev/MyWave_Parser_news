"""Сбор соревнований из внешних календарей → competitions_ticker."""
from __future__ import annotations

import logging
from typing import Any

from config.settings import config
from services.competitions_external_calendars import fetch_external_competition_rows
from services.competitions_ticker_sync import upsert_competition_rows

LOGGER = logging.getLogger(__name__)


async def collect_competitions_from_sources() -> dict[str, Any]:
    """Парсит IWWF/WSWS/WWA iCal/CWSA URL из config и upsert в лист."""
    rows = fetch_external_competition_rows()
    if not rows:
        return {"input": 0, "upserted": 0, "note": "no_events_parsed"}
    stats = await upsert_competition_rows(rows, invalidate_cache=True)
    stats["sources"] = {
        "iwwf": bool(getattr(config, "COMPETITIONS_IWWF_CALENDAR_URL", "")),
        "wsws": bool(getattr(config, "COMPETITIONS_WSWS_CALENDAR_URL", "")),
        "wwa_ical": bool(getattr(config, "COMPETITIONS_WWA_ICAL_URL", "")),
        "cwsa": bool(getattr(config, "COMPETITIONS_CWSA_CALENDAR_URL", "")),
    }
    return stats


__all__ = ["collect_competitions_from_sources"]
