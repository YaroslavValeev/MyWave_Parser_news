import pytest
from collectors.rss_collector import fetch_rss

def test_fetch_rss_basic():
    url = "https://www.wakeboardingmag.com/feed"
    items = fetch_rss(url, "Wakeboarding Magazine")
    assert isinstance(items, list)
    if items:
        item = items[0]
        assert hasattr(item, "id")
        assert hasattr(item, "raw_title")
        assert hasattr(item, "raw_content")
