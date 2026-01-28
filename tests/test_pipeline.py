import pytest
from core.processors.deduplication import add_checksum, is_duplicate
from core.models import NewsItem
from unittest.mock import MagicMock

def test_add_checksum():
    item = NewsItem(
        id="1", source_type="rss", source_name="test", source_url="url",
        created_at="now", raw_title="title", raw_content="content"
    )
    item = add_checksum(item)
    assert item.checksum
    # Checksum must be stable
    item2 = add_checksum(item.copy())
    assert item.checksum == item2.checksum

def test_is_duplicate():
    item = NewsItem(
        id="1", source_type="rss", source_name="test", source_url="url",
        created_at="now", raw_title="title", raw_content="content"
    )
    item = add_checksum(item)
    assert is_duplicate(item, {item.checksum})
    assert not is_duplicate(item, set())


def test_append_news_batch(monkeypatch):
    from storage.google_sheets import GoogleSheets
    from utils.sheet_schema import RAW_FEED_COLUMNS
    gs = GoogleSheets.__new__(GoogleSheets)
    gs.sheet = MagicMock()

    header = RAW_FEED_COLUMNS
    gs.sheet.get_all_values.return_value = [header]
    gs.sheet.row_values.return_value = header

    items = [{
        "id": "1",
        "source_type": "rss",
        "source_name": "test",
        "source_url": "https://example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "ingest_status": "ok",
        "raw_title": "title",
        "raw_content": "content",
        "checksum": "abc",
    }]
    gs.append_news_batch(items)
    assert gs.sheet.append_rows.called
