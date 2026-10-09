import pytest

from config.settings import config
from services.site_cache import (
    cache_invalidate_configured,
    cache_invalidate_url,
    invalidate_site_blog_cache,
)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_cache_invalidate_url_uses_site_base(monkeypatch):
    monkeypatch.setattr(config, "SITE_BASE_URL", "https://mywave.ru")
    monkeypatch.setattr(config, "SITE_CACHE_INVALIDATE_ENDPOINT", "/api/blog/cache/invalidate")
    monkeypatch.setattr(config, "SITE_CACHE_INVALIDATE_TOKEN", "secret-token")

    assert cache_invalidate_configured() is True
    assert cache_invalidate_url() == "https://mywave.ru/api/blog/cache/invalidate"


@pytest.mark.asyncio
async def test_invalidate_site_blog_cache_posts_bearer_token(monkeypatch):
    monkeypatch.setattr(config, "SITE_BASE_URL", "https://mywave.ru")
    monkeypatch.setattr(config, "SITE_CACHE_INVALIDATE_ENDPOINT", "/api/blog/cache/invalidate")
    monkeypatch.setattr(config, "SITE_CACHE_INVALIDATE_TOKEN", "secret-token")
    monkeypatch.setattr(config, "SITE_CACHE_INVALIDATE_TIMEOUT_SECONDS", 12)
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        calls["timeout"] = timeout
        return _Response(202, {"ok": True})

    monkeypatch.setattr("services.site_cache.requests.post", fake_post)

    result = await invalidate_site_blog_cache(item_id=1375, reason="raw_feed_published")

    assert result is True
    assert calls["url"] == "https://mywave.ru/api/blog/cache/invalidate"
    assert calls["headers"]["Authorization"] == "Bearer secret-token"
    assert calls["json"]["item_id"] == "1375"
    assert calls["json"]["reason"] == "raw_feed_published"
    assert calls["timeout"] == 12
