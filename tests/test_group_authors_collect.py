from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from collectors.group_authors import collect_group_message_authors
from services.group_authors_engagement import run_group_authors_collect
from storage.data import count_group_authors, save_group_authors
from storage.repository import initialize_database
from utils.group_authors_contract import make_author_id


class _FakeMsg:
    def __init__(self, mid, sender, text="hi", out=False):
        self.id = mid
        self.sender = sender
        self.message = text
        self.out = out
        self.date = None


@pytest.mark.asyncio
async def test_collect_dedups_authors():
    user_a = SimpleNamespace(id=10, username="alice", bot=False, first_name="A", last_name="")
    user_b = SimpleNamespace(id=11, username="bob", bot=False, first_name="B", last_name="")
    messages = [
        _FakeMsg(3, user_a, "one"),
        _FakeMsg(2, user_b, "two"),
        _FakeMsg(1, user_a, "three"),
    ]

    class _Client:
        async def get_entity(self, _peer):
            return SimpleNamespace(title="Wake Group", megagroup=True, broadcast=False)

        def iter_messages(self, _entity, limit=100, min_id=0):
            async def _gen():
                for m in messages:
                    if m.id > min_id:
                        yield m

            return _gen()

    rows, stats, max_mid = await collect_group_message_authors(
        _Client(),
        group_id="1701957243",
        message_limit=10,
        delay_seconds=0,
        skip_bots=True,
        min_message_id=0,
    )
    assert stats.authors_collected == 2
    assert len(rows) == 2
    assert max_mid == 3
    assert {r["user_id"] for r in rows} == {"10", "11"}
    assert make_author_id(group_id="1701957243", user_id="10") in {
        r["author_id"] for r in rows
    }


@pytest.mark.asyncio
async def test_save_group_authors_repo(tmp_path, monkeypatch):
    from pathlib import Path

    from config.settings import config
    from storage import data as data_mod
    from storage.repository import AsyncNewsRepository

    db = tmp_path / "ga.db"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(data_mod, "DB_PATH", Path(db))
    monkeypatch.setattr(data_mod, "_REPOSITORY", AsyncNewsRepository(db))
    monkeypatch.setattr(data_mod, "_INITIALIZED", False)
    await initialize_database(db)
    n = await save_group_authors(
        [
            {
                "author_id": "x1",
                "group_id": "1701957243",
                "group_title": "Wake",
                "user_id": "10",
                "user_name": "@a",
                "last_message_id": "3",
                "last_message_at": "2026-11-06T00:00:00+00:00",
                "message_snippet": "hi",
                "messages_seen": 1,
                "source_name": "Wake",
            }
        ]
    )
    assert n == 1
    assert await count_group_authors() == 1


@pytest.mark.asyncio
async def test_run_group_authors_falls_back_to_db_sync(monkeypatch):
    monkeypatch.setattr(
        "services.group_authors_engagement._telegram_client",
        AsyncMock(side_effect=RuntimeError("TelegramClient not initialized")),
    )
    monkeypatch.setattr(
        "services.group_authors_engagement.sync_pending_group_authors_to_sheet",
        AsyncMock(return_value={"db_rows": 2, "sheet": {"updated": 1, "appended": 1, "errors": 0}}),
    )

    result = await run_group_authors_collect(sync_sheet=True)
    assert result.saved_db == 0
    assert result.sheet_updated == 1
    assert result.sheet_appended == 1
    assert result.errors == 1
    assert "pending-записей" in result.note
