from __future__ import annotations

import pytest

from storage.sources import NewsSource
from utils.import_asyncio import WebsiteParser


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.mark.asyncio
async def test_website_parser_does_not_save_homepage_description(monkeypatch):
    html = """
    <html>
      <head>
        <title>Wakeboarding, Wakeboard Gear, Videos, Tips, Photos | Wakeboarding Mag</title>
        <meta name="description" content="Wakeboarding Magazine covers the latest in wakeboarding gear, videos, tips, photos, boats, news, and so much more.">
        <meta property="og:image" content="https://www.wakeboardingmag.com/cover.jpg">
      </head>
      <body><main><p>Wakeboarding Magazine covers the latest in wakeboarding gear.</p></main></body>
    </html>
    """

    monkeypatch.setattr(
        "utils.import_asyncio.requests.get",
        lambda *args, **kwargs: _FakeResponse(html),
    )

    source = NewsSource("website", "https://www.wakeboardingmag.com", True, None, "Wakeboarding Magazine")

    assert await WebsiteParser(limit=2).parse(source) == []


@pytest.mark.asyncio
async def test_website_parser_discovers_and_parses_article_links(monkeypatch):
    listing_html = """
    <html><body><main>
      <article>
        <h2><a href="/news/real-wake-event-2026/">Real Wake Event 2026</a></h2>
        <p>Short card text.</p>
      </article>
      <article>
        <h2><a href="/news/second-wake-story/">Second Wake Story</a></h2>
        <p>Second card text.</p>
      </article>
    </main></body></html>
    """
    article_html = """
    <html>
      <head><meta property="og:title" content="Real Wake Event 2026"></head>
      <body>
        <article>
          <h1>Real Wake Event 2026</h1>
          <div class="entry-content"><p>Full article body about the real wake event.</p></div>
          <img src="/media/event.jpg">
        </article>
      </body>
    </html>
    """
    second_html = """
    <html><body><article><h1>Second Wake Story</h1><p>Full second article body.</p></article></body></html>
    """

    def fake_get(url: str, *args, **kwargs):
        if url == "https://example.com/blog":
            return _FakeResponse(listing_html)
        if url == "https://example.com/news/real-wake-event-2026/":
            return _FakeResponse(article_html)
        if url == "https://example.com/news/second-wake-story/":
            return _FakeResponse(second_html)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("utils.import_asyncio.requests.get", fake_get)

    source = NewsSource("website", "https://example.com/blog", True, None, "Example Blog")
    items = await WebsiteParser(limit=2).parse(source)

    assert len(items) == 2
    assert items[0]["source_url"] == "https://example.com/news/real-wake-event-2026/"
    assert items[0]["raw_title"] == "Real Wake Event 2026"
    assert "Full article body" in items[0]["raw_content"]
    assert items[0]["cover_image_url"] == "https://example.com/media/event.jpg"
    assert items[1]["source_url"] == "https://example.com/news/second-wake-story/"

