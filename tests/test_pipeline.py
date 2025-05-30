import pytest
from collectors.rss_collector import fetch_rss
from core.processors.deduplication import add_checksum, is_duplicate
from core.models import NewsItem
from unittest.mock import MagicMock

@pytest.mark.asyncio
async def test_fetch_rss():
    url = "https://www.wakeboardingmag.com/feed"
    items = fetch_rss(url, "Wakeboarding Magazine")
    assert isinstance(items, list)
    if items:
        item = items[0]
        assert hasattr(item, "id")
        assert hasattr(item, "raw_title")
        assert hasattr(item, "raw_content")


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
    from services.google_sheets import GoogleSheets
    gs = GoogleSheets.__new__(GoogleSheets)
    gs.sheet = MagicMock()
    items = [NewsItem(
        id="1", source_type="rss", source_name="test", source_url="url",
        created_at="now", raw_title="title", raw_content="content"
    )]
    gs.append_news_batch(items)
    assert gs.sheet.append_rows.called


def test_dry_run_chain():
    url = "https://www.wakeboardingmag.com/feed"
    news = fetch_rss(url, "Wakeboarding Magazine")
    assert isinstance(news, list)
    if news:
        item = add_checksum(news[0])
        assert item.checksum
        assert not is_duplicate(item, set())
        # Симуляция batch (без Google Sheets)
        batch = [add_checksum(n) for n in news[:3]]
        checksums = set()
        for n in batch:
            assert not is_duplicate(n, checksums)
            checksums.add(n.checksum)
