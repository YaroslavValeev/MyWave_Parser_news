from unittest.mock import AsyncMock

import pytest

from services.channel_engagement import run_channel_engagement


@pytest.mark.asyncio
async def test_run_channel_engagement_falls_back_to_db_sync(monkeypatch):
    monkeypatch.setattr(
        "services.channel_engagement._telegram_client",
        AsyncMock(side_effect=RuntimeError("TelegramClient not initialized")),
    )
    monkeypatch.setattr(
        "services.channel_engagement.sync_pending_commenters_to_sheet",
        AsyncMock(return_value={"db_rows": 2, "sheet": {"updated": 1, "appended": 1, "errors": 0}}),
    )

    result = await run_channel_engagement(sync_sheet=True)

    assert result.saved_db == 0
    assert result.sheet_updated == 1
    assert result.sheet_appended == 1
    assert result.errors == 1
    assert "pending-записей" in result.note


@pytest.mark.asyncio
async def test_run_channel_engagement_falls_back_without_pending(monkeypatch):
    monkeypatch.setattr(
        "services.channel_engagement._telegram_client",
        AsyncMock(side_effect=RuntimeError("TelegramClient not initialized")),
    )
    monkeypatch.setattr(
        "services.channel_engagement.sync_pending_commenters_to_sheet",
        AsyncMock(return_value={"db_rows": 0, "sheet": {}}),
    )

    result = await run_channel_engagement(sync_sheet=True)

    assert result.saved_db == 0
    assert result.sheet_updated == 0
    assert result.sheet_appended == 0
    assert result.errors == 1
    assert "pending-записей в БД нет" in result.note
