from __future__ import annotations

import asyncio

import pytest
from aiogram.fsm.storage.base import StorageKey

from storage.fsm_sqlite import SQLiteTTLStorage


@pytest.mark.asyncio
async def test_sqlite_fsm_storage_persists_state_and_data(tmp_path):
    db_path = tmp_path / "fsm.db"
    storage = SQLiteTTLStorage(db_path=db_path, ttl_seconds=3600)
    key = StorageKey(bot_id=1, chat_id=10, user_id=20, thread_id=None, destiny="default")

    await storage.set_state(key, "ReviewCommentForm:waiting_text")
    await storage.set_data(key, {"item_id": 123, "mode": "comment"})

    assert await storage.get_state(key) == "ReviewCommentForm:waiting_text"
    assert await storage.get_data(key) == {"item_id": 123, "mode": "comment"}
    await storage.close()


@pytest.mark.asyncio
async def test_sqlite_fsm_storage_expires_records(tmp_path):
    db_path = tmp_path / "fsm.db"
    storage = SQLiteTTLStorage(db_path=db_path, ttl_seconds=1)
    key = StorageKey(bot_id=2, chat_id=30, user_id=40, thread_id=None, destiny="default")

    await storage.set_state(key, "ProbeForm:waiting_url")
    await storage.set_data(key, {"url": "https://example.com"})
    await asyncio.sleep(1.2)

    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {}
    await storage.close()

