from datetime import date, timedelta

from utils.competitions_contract import (
    acceptance_test_rows,
    build_ticker_text,
    competition_display_phase,
    competition_is_live,
    filter_competition_rows_for_window,
    normalize_competition_row,
    should_archive_row,
    validate_competition_row,
)


def test_validate_competition_row_ok():
    row = normalize_competition_row(
        {
            "id": "iwwf-2026-1",
            "status": "ACTIVE",
            "discipline": "wakesurf",
            "event_name": "World Cup",
            "location": "Orlando",
            "country": "USA",
            "start_date": "2026-06-12",
            "end_date": "2026-06-15",
            "event_url": "https://example.com/event",
            "source_name": "IWWF",
            "source_url": "https://iwwf.sport",
        }
    )
    ok, reason = validate_competition_row(row)
    assert ok is True
    assert reason == ""


def test_normalize_competition_row_fills_missing_source_url_from_event_url():
    row = normalize_competition_row(
        {
            "id": "evt-1",
            "status": "ACTIVE",
            "discipline": "wakeboard",
            "event_name": "MyWave Sochi 2026",
            "start_date": "2026-09-01",
            "event_url": "https://example.com/real-event",
            "source_name": "mywave",
            "source_url": "",
        }
    )
    assert row["event_url"] == "https://example.com/real-event"
    assert row["source_url"] == "https://example.com/real-event"


def test_normalize_competition_row_replaces_placeholder_source_url():
    row = normalize_competition_row(
        {
            "id": "evt-2",
            "status": "ACTIVE",
            "discipline": "wakeboard",
            "event_name": "MyWave Sochi 2026",
            "start_date": "2026-09-01",
            "event_url": "https://example.com/real-event",
            "source_name": "mywave",
            "source_url": "https://REAL-SOURCE-URL-HERE",
        }
    )
    assert row["source_url"] == "https://example.com/real-event"


def test_build_ticker_text_auto():
    row = normalize_competition_row(
        {
            "id": "x",
            "status": "ACTIVE",
            "discipline": "wakesurf",
            "event_name": "IWWF World Championships",
            "location": "Orlando",
            "country": "USA",
            "start_date": "2026-06-12",
            "end_date": "2026-06-15",
            "event_url": "https://example.com",
            "source_name": "IWWF",
            "source_url": "https://iwwf.sport",
        }
    )
    text = build_ticker_text(row)
    assert "Wakesurf" in text
    assert "IWWF World Championships" in text
    assert "Orlando" in text
    assert "12.06" in text


def test_should_archive_past_end_date():
    today = date(2026, 5, 6)
    past = {
        "id": "old",
        "status": "ACTIVE",
        "end_date": (today - timedelta(days=1)).isoformat(),
    }
    assert should_archive_row(past, today=today) is True


def test_acceptance_rows_three_ids():
    rows = acceptance_test_rows()
    assert len(rows) == 3
    ids = {r["id"] for r in rows}
    assert ids == {"test-1", "test-2", "test-3"}
    assert rows[0]["status"] == "ACTIVE"
    assert rows[2]["status"] == "ARCHIVED"


def test_competition_is_live_and_display_phase():
    today = date(2026, 6, 8)
    live_row = normalize_competition_row(
        {
            "id": "live-1",
            "status": "ACTIVE",
            "discipline": "wakesurf",
            "event_name": "Cyprus Open",
            "start_date": "2026-06-06",
            "end_date": "2026-06-10",
            "event_url": "https://example.com/live",
            "source_url": "https://example.com/live",
            "source_name": "src",
        }
    )
    future_row = normalize_competition_row(
        {
            "id": "future-1",
            "status": "ACTIVE",
            "discipline": "wakeboard",
            "event_name": "Future Open",
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "event_url": "https://example.com/future",
            "source_url": "https://example.com/future",
            "source_name": "src",
        }
    )
    assert competition_is_live(live_row, today=today) is True
    assert live_row["is_live"] == "true"
    assert live_row["display_phase"] == "live"
    assert competition_is_live(future_row, today=today) is False
    assert future_row["display_phase"] == "upcoming"
    assert competition_display_phase(future_row, today=today) == "upcoming"


def test_filter_competition_rows_for_window():
    today = date(2026, 6, 1)
    max_end = date(2026, 9, 10)
    rows = filter_competition_rows_for_window(
        [
            {
                "id": "a",
                "event_name": "Soon Cup",
                "discipline": "wakesurf",
                "start_date": "2026-06-06",
                "end_date": "2026-06-07",
                "event_url": "https://example.com/a",
                "source_url": "https://example.com/a",
                "source_name": "src",
            },
            {
                "id": "b",
                "event_name": "Too Late Open",
                "discipline": "wakeboard",
                "start_date": "2026-10-01",
                "end_date": "2026-10-05",
                "event_url": "https://example.com/b",
                "source_url": "https://example.com/b",
                "source_name": "src",
            },
            {
                "id": "c",
                "event_name": "Past Event",
                "discipline": "wakeboard",
                "start_date": "2026-03-01",
                "end_date": "2026-03-02",
                "event_url": "https://example.com/c",
                "source_url": "https://example.com/c",
                "source_name": "src",
            },
        ],
        max_end_date=max_end,
        today=today,
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "a"
    assert rows[0]["status"] == "ACTIVE"
