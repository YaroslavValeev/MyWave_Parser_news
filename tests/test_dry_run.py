import pytest
from collectors.rss_collector import fetch_rss
from core.processors.deduplication import add_checksum, is_duplicate
from core.models import NewsItem

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
