from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.competitions_ticker_sync import archive_acceptance_test_rows, upsert_competition_rows


@pytest.mark.asyncio
async def test_upsert_skips_when_no_doc():
    stats = await upsert_competition_rows([{"id": "x"}], invalidate_cache=False)
    assert stats["errors"] == 1


@pytest.mark.asyncio
async def test_upsert_update_path(monkeypatch):
    doc = MagicMock()

    async def fake_get_doc():
        return doc

    monkeypatch.setattr(
        "services.competitions_ticker_sync._get_doc",
        fake_get_doc,
    )
    monkeypatch.setattr(
        "services.competitions_ticker_sync.ensure_competitions_sheet_headers",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.competitions_ticker_sync.update_item",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.competitions_ticker_sync.invalidate_competitions_cache",
        AsyncMock(return_value=True),
    )

    row = {
        "id": "evt-1",
        "status": "ACTIVE",
        "discipline": "wakesurf",
        "event_name": "Cup",
        "location": "City",
        "country": "RU",
        "start_date": "2026-08-01",
        "end_date": "2026-08-02",
        "event_url": "https://example.com/e",
        "source_name": "src",
        "source_url": "https://example.com",
    }
    stats = await upsert_competition_rows([row], invalidate_cache=True)
    assert stats["upserted"] == 1
    assert stats["updated"] == 1


@pytest.mark.asyncio
async def test_archive_acceptance_sets_archived(monkeypatch):
    doc = MagicMock()
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {
            "id": "test-1",
            "status": "ACTIVE",
            "discipline": "wakesurf",
            "event_name": "Parser Acceptance — Future Event A",
            "location": "Orlando",
            "country": "USA",
            "start_date": "2026-06-20",
            "end_date": "2026-06-23",
            "event_url": "https://example.com/events/test-1",
            "source_name": "parser_acceptance",
            "source_url": "https://example.com/sources/parser",
        }
    ]

    async def fake_get_doc():
        return doc

    captured: list[dict] = []

    async def fake_update(_doc, _sheet, payload, lookup_field="id"):
        captured.append(dict(payload))
        return True

    monkeypatch.setattr("services.competitions_ticker_sync._get_doc", fake_get_doc)
    monkeypatch.setattr(
        "services.competitions_ticker_sync.get_worksheet",
        lambda _d, _s: ws,
    )
    monkeypatch.setattr(
        "services.competitions_ticker_sync.ensure_competitions_sheet_headers",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("services.competitions_ticker_sync.update_item", fake_update)
    monkeypatch.setattr(
        "services.competitions_ticker_sync.invalidate_competitions_cache",
        AsyncMock(return_value=True),
    )

    stats = await archive_acceptance_test_rows(invalidate_cache=False)
    assert stats["upserted"] == 3
    test1 = next(p for p in captured if p["id"] == "test-1")
    assert test1["status"] == "ARCHIVED"
    assert "Future Event A" in test1["event_name"]
