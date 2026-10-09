from unittest.mock import AsyncMock, patch

import pytest

from services.competitions_sources_collect import collect_competitions_from_sources


@pytest.mark.asyncio
async def test_collect_competitions_empty_urls(monkeypatch):
    monkeypatch.setattr(
        "services.competitions_sources_collect.fetch_calendar_events",
        lambda *a, **k: [],
    )
    stats = await collect_competitions_from_sources()
    assert stats["input"] == 0


@pytest.mark.asyncio
async def test_collect_competitions_upserts(monkeypatch):
    monkeypatch.setattr(
        "services.competitions_sources_collect.fetch_calendar_events",
        lambda *a, **k: [{"id": "iwwf-x", "status": "ACTIVE", "event_name": "E", "start_date": "2099-01-01", "end_date": "2099-01-02"}],
    )
    monkeypatch.setattr(
        "services.competitions_sources_collect.upsert_competition_rows",
        AsyncMock(return_value={"input": 1, "upserted": 1}),
    )
    stats = await collect_competitions_from_sources()
    assert stats["upserted"] == 1
