import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.publication import PublicationService, PublicationSendError
from storage.repository import AsyncNewsRepository


class DummyRepo(AsyncNewsRepository):
    def __init__(self):
        # do not call parent
        self._store = {}

    async def list_publication_candidates(self, limit=10):
        return [
            {"id": 1, "title": "T", "content": "C", "link": "http://x"}
        ]

    async def get_nlp_results(self, item_id: int):
        return {"summary": "short summary"}

    async def update_status(self, item_id: int, status: str):
        self._store.setdefault(item_id, {})["status"] = status

    async def save_publication(self, item_id: int, channel_id: str, message_id: str):
        self._store.setdefault(item_id, {})["pub"] = (channel_id, message_id)

    async def log_event(self, item_id, level, message, meta=None):
        self._store.setdefault(item_id, {}).setdefault("logs", []).append((level, message, meta))


@pytest.mark.asyncio
async def test_publish_success():
    repo = DummyRepo()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")
    published = await svc.publish_pending(limit=1)
    assert published == 1


@pytest.mark.asyncio
async def test_publish_failure_retry():
    repo = DummyRepo()
    class BadBot:
        async def send_message(self, **kwargs):
            raise Exception("boom")

    bot = BadBot()
    svc = PublicationService(repo, bot, channel_id="42")
    # Configure low attempts to force marking error after failure
    svc._max_attempts = 1
    published = await svc.publish_pending(limit=1)
    assert published == 0
