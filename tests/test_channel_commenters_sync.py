from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.user_messages_sync import sync_records_to_user_messages


@pytest.mark.asyncio
async def test_sync_records_skips_without_gateway(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    stats = await sync_records_to_user_messages([])
    assert stats["input"] == 0


@pytest.mark.asyncio
async def test_sync_records_update_and_append(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    doc = MagicMock()
    monkeypatch.setattr(
        "services.user_messages_sync.init_sheet_gateway",
        AsyncMock(return_value=doc),
    )
    monkeypatch.setattr(
        "services.user_messages_sync.ensure_user_messages_headers",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.user_messages_sync.update_item",
        AsyncMock(side_effect=[True, False]),
    )
    ws = MagicMock()
    monkeypatch.setattr("services.user_messages_sync.get_worksheet", lambda *_: ws)
    records = [
        {
            "message_id": "1",
            "user_id": "10",
            "user_name": "@a",
            "post_id": "100",
            "comment_text": "x",
            "comment_at": "2026-08-01",
        },
        {
            "message_id": "2",
            "user_id": "11",
            "user_name": "@b",
            "post_id": "101",
            "comment_text": "y",
            "comment_at": "2026-08-02",
        },
    ]
    stats = await sync_records_to_user_messages(records)
    assert stats["updated"] == 1
    assert stats["appended"] == 1
    ws.append_rows.assert_called_once()


@pytest.mark.asyncio
async def test_save_channel_commenters_repo(tmp_path, monkeypatch):
    from config.settings import config
    from storage.data import save_channel_commenters, count_channel_commenters
    from storage.repository import initialize_database

    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    await initialize_database(db)
    n = await save_channel_commenters(
        [
            {
                "commenter_id": "abc",
                "channel_url": "https://t.me/x",
                "channel_title": "X",
                "post_id": "1",
                "message_id": "2",
                "user_id": "3",
                "user_name": "@u",
                "comment_text": "hi",
                "comment_at": "2026-08-01",
                "source_name": "x",
            }
        ]
    )
    assert n == 1
    assert await count_channel_commenters() == 1


@pytest.mark.asyncio
async def test_sync_unsynced_does_not_mark_synced_on_errors(monkeypatch):
    from services.user_messages_sync import sync_unsynced_from_db

    repo = MagicMock()
    repo.list_channel_commenters_unsynced = AsyncMock(
        return_value=[{"commenter_id": "abc", "message_id": "1"}]
    )
    repo.mark_channel_commenters_synced = AsyncMock()
    monkeypatch.setattr("storage.data.get_repository", AsyncMock(return_value=repo))
    monkeypatch.setattr(
        "services.user_messages_sync.sync_records_to_user_messages",
        AsyncMock(return_value={"updated": 0, "appended": 0, "errors": 1}),
    )

    result = await sync_unsynced_from_db()

    assert result["db_rows"] == 1
    repo.mark_channel_commenters_synced.assert_not_called()
