"""Сбор строк competitions_ticker из внешних календарей (IWWF, WWA iCal, CWSA, WSWS)."""
from __future__ import annotations

from typing import Any

from config.settings import config
from collectors.competitions_html_calendar import fetch_calendar_events
from collectors.competitions_ical_calendar import fetch_ical_calendar_events


def fetch_external_competition_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    iwwf_url = str(getattr(config, "COMPETITIONS_IWWF_CALENDAR_URL", "") or "").strip()
    wsws_url = str(getattr(config, "COMPETITIONS_WSWS_CALENDAR_URL", "") or "").strip()
    wwa_ical = str(getattr(config, "COMPETITIONS_WWA_ICAL_URL", "") or "").strip()
    cwsa_url = str(getattr(config, "COMPETITIONS_CWSA_CALENDAR_URL", "") or "").strip()

    if iwwf_url:
        rows.extend(fetch_calendar_events(iwwf_url, source_name="iwwf", discipline="wakeboard"))
    if wsws_url:
        rows.extend(fetch_calendar_events(wsws_url, source_name="wsws", discipline="wakesurf"))
    if wwa_ical:
        rows.extend(fetch_ical_calendar_events(wwa_ical, source_name="wwa", discipline="both"))
    if cwsa_url:
        rows.extend(fetch_calendar_events(cwsa_url, source_name="cwsa", discipline="wakesurf"))

    return rows


__all__ = ["fetch_external_competition_rows"]
