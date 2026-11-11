from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import manual_collect
from services.manual_collect import (
    ManualSource,
    _convert_raw_entry,
    parse_period_argument,
)


def test_parse_period_argument():
    assert isinstance(parse_period_argument("1d"), datetime)
    assert isinstance(parse_period_argument("3h"), datetime)


def test_storage_conversion_preserves_text_and_splits_image_video():
    source = ManualSource(type="telegram", url="https://t.me/example", name="Example")
    value = _convert_raw_entry(
        {
            "raw_title": "Событие",
            "raw_content": "Факты исходного поста",
            "source_url": "https://t.me/example",
            "link": "https://t.me/example/123",
            "raw_media": '["downloads/photo.jpg","downloads/video.mp4?size=1"]',
            "created_at": "2026-10-09T03:00:00+03:00",
        },
        source,
    )
    assert value["content"] == "Факты исходного поста"
    assert value["images"] == "downloads/photo.jpg"
    assert value["videos"] == "downloads/video.mp4?size=1"
    assert value["link"] == "https://t.me/example/123"
    assert value["date"] == "2026-10-09T00:00:00+00:00"


@pytest.mark.asyncio
async def test_injected_telegram_client_is_preserved_and_contacts_disabled(monkeypatch):
    client = SimpleNamespace(disconnect=AsyncMock())
    calls = []

    class Parser:
        def __init__(self, limit):
            calls.append(limit)

        async def parse(self, given, source, *, download_media):
            assert given is client and download_media is False
            yield {
                "raw_title": "Новости",
                "raw_content": "Текст поста",
                "source_url": "https://t.me/example/123",
            }

    def forbidden():
        raise AssertionError("must not start heavy contacts collection")

    monkeypatch.setattr(manual_collect, "_load_telethon_parser", lambda: Parser)
    monkeypatch.setattr(manual_collect, "_load_contacts_parser", forbidden)
    monkeypatch.setattr(manual_collect.config, "COLLECT_CONTACTS_ON_FULL_PARSE", False)
    items, contacts = await manual_collect._fetch_telegram_items(
        ManualSource(type="telegram", url="https://t.me/example", name="Example"),
        2,
        download_media=False,
        telegram_client=client,
    )
    assert calls == [2] and contacts == [] and items[0]["content"] == "Текст поста"
    client.disconnect.assert_not_awaited()
