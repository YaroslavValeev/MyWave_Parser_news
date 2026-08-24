"""Tests for Content Engine Stage 1 source telemetry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.source_telemetry import (
    SourceTickMetrics,
    evaluate_source_pipeline,
    source_key,
)
from storage.repository import AsyncNewsRepository, initialize_database


def test_format_source_health_html_empty():
    from services.source_telemetry import format_source_health_html

    html = format_source_health_html(None)
    assert "Source health" in html
    assert "Пока нет данных" in html


def test_format_source_health_html_with_rows():
    from services.source_telemetry import format_source_health_html

    html = format_source_health_html(
        [
            {
                "source_name": "demo-feed",
                "source_key": "rss|demo",
                "last_ok": 1,
                "last_collected": 3,
                "last_duplicates": 1,
                "last_errors": 0,
                "last_latency_ms": 42,
                "consecutive_failures": 0,
                "last_success_at": "2099-01-01T00:00:00+00:00",
                "errors_total": 0,
            }
        ],
        stale_hours=36,
        fail_streak=3,
    )
    assert "demo-feed" in html
    assert "pipeline" in html.lower() or "Pipeline" in html


def test_source_key_stable():
    a = source_key("rss", "https://Example.com/Feed/", "Name")
    b = source_key("RSS", "https://example.com/Feed", "Other")
    assert a == b
    assert a.startswith("rss|")


def test_tick_to_result_row():
    tick = SourceTickMetrics(
        source_type="rss",
        source_name="demo",
        source_url="https://demo.example/feed",
        ok=True,
        latency_ms=12.34,
        collected=5,
        parsed=5,
        saved=2,
        duplicates=3,
    )
    row = tick.to_result_row()
    assert row["news_saved"] == 2
    assert row["duplicates"] == 3
    assert row["latency_ms"] == 12.3


def test_evaluate_fail_streak():
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source_name": "bad",
            "consecutive_failures": 3,
            "last_success_at": (now - timedelta(hours=1)).isoformat(),
            "last_ok": 0,
            "errors_total": 3,
        }
    ]
    v = evaluate_source_pipeline(rows, stale_hours=36, fail_streak=3, now=now)
    assert v["content_pipeline_ok"] is False
    assert "bad" in v["fail_streak_sources"]


def test_evaluate_ok_recent():
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source_name": "good",
            "consecutive_failures": 0,
            "last_success_at": (now - timedelta(hours=1)).isoformat(),
            "last_ok": 1,
            "errors_total": 0,
        }
    ]
    v = evaluate_source_pipeline(rows, stale_hours=36, fail_streak=3, now=now)
    assert v["content_pipeline_ok"] is True
    assert v["sources_ok_recent"] == 1


@pytest.mark.asyncio
async def test_upsert_source_health_persists(tmp_path):
    db = tmp_path / "health.sqlite"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    await repo.upsert_source_health(
        {
            "source_key": "rss|demo.example/feed",
            "source_type": "rss",
            "source_name": "demo",
            "source_url": "https://demo.example/feed",
            "ok": True,
            "latency_ms": 40.0,
            "collected": 4,
            "parsed": 4,
            "duplicates": 1,
            "rejected": 0,
            "errors": 0,
            "error": "",
        }
    )
    row = await repo.get_source_health("rss|demo.example/feed")
    assert row is not None
    assert row["last_ok"] == 1
    assert int(row["last_collected"]) == 4
    assert int(row["duplicates_total"]) == 1
    assert row["last_success_at"]

    await repo.upsert_source_health(
        {
            "source_key": "rss|demo.example/feed",
            "source_type": "rss",
            "source_name": "demo",
            "source_url": "https://demo.example/feed",
            "ok": False,
            "latency_ms": 10.0,
            "collected": 0,
            "parsed": 0,
            "duplicates": 0,
            "rejected": 0,
            "errors": 1,
            "error": "Boom",
        }
    )
    row2 = await repo.get_source_health("rss|demo.example/feed")
    assert row2 is not None
    assert int(row2["consecutive_failures"]) == 1
    assert int(row2["collected_total"]) == 4
    assert int(row2["errors_total"]) == 1
    assert row2["last_success_at"]  # preserved
