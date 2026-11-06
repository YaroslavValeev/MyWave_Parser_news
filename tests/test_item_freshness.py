from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from storage.repository import AsyncNewsRepository, initialize_database
from utils.item_freshness import is_item_stale_for_review, normalize_item_date, review_max_age_days


def test_normalize_item_date_iso():
    assert normalize_item_date("2026-04-01T12:00:00+00:00") == "2026-04-01T12:00:00+00:00"


def test_is_item_stale_for_review_respects_cutoff():
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    fresh = {"date": (now - timedelta(days=10)).isoformat()}
    stale = {"date": (now - timedelta(days=45)).isoformat()}
    assert not is_item_stale_for_review(fresh, max_days=30, now=now)
    assert is_item_stale_for_review(stale, max_days=30, now=now)


def test_unknown_publication_date_is_not_stale():
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert not is_item_stale_for_review({"title": "no date"}, max_days=30, now=now)


@pytest.mark.asyncio
async def test_list_review_queue_skips_stale_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.item_freshness.config.REVIEW_MAX_AGE_DAYS", 30)
    db_file = tmp_path / "stale.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    now = datetime.now(timezone.utc)
    fresh_id = await repo.create_item(
        {
            "source": "rss:Test",
            "title": "Fresh",
            "content": "body",
            "link": "https://example.com/fresh",
            "status": "review",
            "date": (now - timedelta(days=5)).isoformat(),
        }
    )
    stale_id = await repo.create_item(
        {
            "source": "rss:Test",
            "title": "Stale",
            "content": "body",
            "link": "https://example.com/stale",
            "status": "review",
            "date": (now - timedelta(days=40)).isoformat(),
        }
    )
    queue = await repo.list_review_queue(limit=10)
    assert [row["id"] for row in queue] == [fresh_id]
    stale = await repo.get_item(stale_id)
    assert stale and stale["status"] == "expired"
    assert review_max_age_days() == 30
