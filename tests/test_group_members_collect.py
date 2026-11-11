from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from collectors.group_members import collect_group_members
from services.group_members_engagement import run_group_members_collect
from storage.data import count_group_members, save_group_members
from storage.repository import initialize_database
from utils.group_members_contract import make_member_id


@pytest.mark.asyncio
async def test_collect_group_members_megagroup():
    users = [
        SimpleNamespace(id=10, username="alice", bot=False, first_name="A", last_name=""),
        SimpleNamespace(id=11, username="bob", bot=False, first_name="B", last_name=""),
        SimpleNamespace(id=12, username="botty", bot=True, first_name="Bot", last_name=""),
    ]

    class _Client:
        async def get_entity(self, _peer):
            return SimpleNamespace(
                id=1609224876,
                title="SportStation",
                username="sportstation_club",
                megagroup=True,
                broadcast=False,
            )

        def iter_participants(self, _entity, limit=500, filter=None):
            async def _gen():
                if filter is not None:
                    yield users[0]  # alice as admin
                    return
                for u in users:
                    yield u

            return _gen()

    rows, stats = await collect_group_members(
        _Client(),
        group_id="@sportstation_club",
        member_limit=50,
        delay_seconds=0,
        skip_bots=True,
    )
    assert stats.members_collected == 2
    assert len(rows) == 2
    assert {r["user_id"] for r in rows} == {"10", "11"}
    assert any(r["is_admin"] == 1 for r in rows if r["user_id"] == "10")
    assert make_member_id(group_id="@sportstation_club", user_id="10") in {
        r["member_id"] for r in rows
    }


@pytest.mark.asyncio
async def test_collect_skips_broadcast_without_discussion(monkeypatch):
    class _Client:
        async def get_entity(self, _peer):
            return SimpleNamespace(
                id=1124621462,
                title="WakeDivision",
                username="wakedivision",
                megagroup=False,
                broadcast=True,
            )

        def iter_participants(self, *_a, **_k):
            raise AssertionError("must not iterate broadcast roster")

    async def _no_linked(_client, _entity):
        return None

    monkeypatch.setattr(
        "collectors.group_members._linked_discussion",
        _no_linked,
    )

    rows, stats = await collect_group_members(
        _Client(),
        group_id="@wakedivision",
        delay_seconds=0,
    )
    assert rows == []
    assert stats.skipped_broadcast == 1
    assert stats.members_collected == 0


@pytest.mark.asyncio
async def test_collect_uses_linked_discussion_for_broadcast(monkeypatch):
    channel = SimpleNamespace(
        id=1609224876,
        title="SportStation",
        username="sportstation_club",
        megagroup=False,
        broadcast=True,
    )
    discussion = SimpleNamespace(
        id=999001,
        title="SportStation Chat",
        username=None,
        megagroup=True,
        broadcast=False,
    )
    users = [
        SimpleNamespace(id=21, username="ann", bot=False, first_name="Ann", last_name=""),
        SimpleNamespace(id=22, username="bob", bot=False, first_name="Bob", last_name=""),
    ]

    class _Client:
        async def get_entity(self, _peer):
            return channel

        def iter_participants(self, entity, limit=500, filter=None):
            assert entity is discussion

            async def _gen():
                if filter is not None:
                    return
                for u in users:
                    yield u

            return _gen()

    async def _linked(_client, _entity):
        return discussion

    monkeypatch.setattr(
        "collectors.group_members._linked_discussion",
        _linked,
    )

    rows, stats = await collect_group_members(
        _Client(),
        group_id="@sportstation_club",
        delay_seconds=0,
        skip_bots=True,
    )
    assert stats.skipped_broadcast == 0
    assert stats.members_collected == 2
    assert {r["group_id"] for r in rows} == {"@sportstation_club"}
    assert {r["user_id"] for r in rows} == {"21", "22"}


@pytest.mark.asyncio
async def test_save_group_members_repo(tmp_path, monkeypatch):
    from pathlib import Path

    from config.settings import config
    from storage import data as data_mod
    from storage.repository import AsyncNewsRepository

    db = tmp_path / "gm.db"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(data_mod, "DB_PATH", Path(db))
    monkeypatch.setattr(data_mod, "_REPOSITORY", AsyncNewsRepository(db))
    monkeypatch.setattr(data_mod, "_INITIALIZED", False)
    await initialize_database(db)
    n = await save_group_members(
        [
            {
                "member_id": "m1",
                "group_id": "@sportstation_club",
                "group_title": "SportStation",
                "user_id": "10",
                "user_name": "@alice",
                "is_bot": 0,
                "is_admin": 1,
                "source_name": "SportStation",
            }
        ]
    )
    assert n == 1
    assert await count_group_members() == 1


@pytest.mark.asyncio
async def test_run_group_members_falls_back_to_db_sync(monkeypatch):
    monkeypatch.setattr(
        "services.group_members_engagement._telegram_client",
        AsyncMock(side_effect=RuntimeError("TelegramClient not initialized")),
    )
    monkeypatch.setattr(
        "services.group_members_engagement.sync_pending_group_members_to_sheet",
        AsyncMock(
            return_value={
                "db_rows": 2,
                "sheet": {"updated": 1, "appended": 1, "errors": 0},
            }
        ),
    )

    result = await run_group_members_collect(sync_sheet=True)
    assert result.saved_db == 0
    assert result.sheet_updated == 1
    assert result.sheet_appended == 1
    assert result.errors == 1
    assert "pending-записей" in result.note
