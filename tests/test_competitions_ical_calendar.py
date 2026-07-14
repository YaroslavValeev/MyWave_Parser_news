"""Тесты парсера iCal календаря соревнований."""
from datetime import date

from collectors.competitions_ical_calendar import ical_url_to_https, parse_ical_events


SAMPLE_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Nautique Southeast Regatta
DTSTART;VALUE=DATE:20260612
DTEND;VALUE=DATE:20260615
LOCATION:Buford\\, GA
URL:https://www.thewwa.com/2026-nautique-regatta/
END:VEVENT
BEGIN:VEVENT
SUMMARY:Past Event
DTSTART;VALUE=DATE:20200101
DTEND;VALUE=DATE:20200102
END:VEVENT
END:VCALENDAR
"""


def test_ical_url_to_https():
    assert ical_url_to_https("webcal://www.thewwa.com/foo").startswith("https://")


def test_parse_ical_events_skips_past(monkeypatch):
    import collectors.competitions_ical_calendar as cal_mod
    from datetime import datetime, timezone

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(cal_mod, "datetime", FixedDatetime)
    rows = parse_ical_events(
        SAMPLE_ICS,
        source_name="wwa",
        discipline="both",
        source_url="https://www.thewwa.com/?ical=1",
    )
    assert len(rows) == 1
    assert rows[0]["event_name"] == "Nautique Southeast Regatta"
    assert rows[0]["start_date"] == "2026-06-12"
    assert rows[0]["id"].startswith("wwa-")


def test_parse_ical_events_empty():
    assert parse_ical_events("", source_name="wwa") == []
