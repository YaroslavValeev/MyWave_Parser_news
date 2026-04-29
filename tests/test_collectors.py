import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collectors.telegram_collector import collect_telegram_news
from collectors.rss_parser import parse_rss
from collectors.youtube_parser import parse_youtube
from collectors.website_collector import parse_website
from storage.sources import NewsSource

@pytest.mark.asyncio
async def test_parse_telegram():
    """Тест парсинга Telegram-канала с заглушкой клиента."""
    source = NewsSource("telegram", "https://t.me/example_channel", True, None, "Example Channel")
    
    mock_client = AsyncMock()
    mock_client.get_entity.return_value = MagicMock()
    message = MagicMock()
    message.text = "Wake surf test news"
    message.id = 7
    message.date = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    message.media = None

    async def iter_messages(*args, **kwargs):
        yield message

    mock_client.iter_messages = iter_messages
    
    try:
        results = await collect_telegram_news(mock_client, source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0  # Проверяем, что новости парсятся
        assert all("title" in item and "content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в collect_telegram_news: {e}")

def test_parse_rss():
    """Тест парсинга RSS-ленты."""
    source = NewsSource("rss", "https://www.wakeboardingmag.com/feed", True, None, "Wakeboarding Magazine")
    feed_entry = {
        "id": "rss-1",
        "title": "Wake news",
        "link": "https://example.com/rss-1",
        "summary": "Wake surf content",
        "published": "2026-01-01",
    }

    try:
        with patch("collectors.rss_parser.feedparser.parse", return_value=MagicMock(entries=[feed_entry])):
            results = parse_rss(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("raw_title" in item and "raw_content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_rss: {e}")

def test_parse_youtube():
    """Тест парсинга YouTube-канала."""
    source = NewsSource("youtube", "https://www.youtube.com/channel/UCJluNGyCBXAR6-CHPRMrZUw", True, None, "JB O'Neill")
    feed_entry = {
        "title": "Wake video",
        "link": "https://www.youtube.com/watch?v=abc",
        "summary": "Wake surf video",
        "published": "2026-01-01",
        "media_thumbnail": [],
    }

    try:
        with patch("collectors.youtube_parser.feedparser.parse", return_value=MagicMock(entries=[feed_entry])):
            results = parse_youtube(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("raw_title" in item and "raw_content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_youtube: {e}")

def test_parse_website():
    """Тест парсинга веб-сайтов."""
    source = NewsSource("website", "https://www.wakeboardingmag.com", True, None, "Wakeboarding Magazine")
    html = """
    <html><body>
      <article><h2>Wake site</h2><p>Wake surf article content</p><a href="/post">Read</a></article>
    </body></html>
    """
    response = MagicMock(text=html)
    response.raise_for_status.return_value = None

    try:
        with patch("collectors.website_collector.requests.get", return_value=response):
            results = parse_website(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("raw_title" in item and "raw_content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_website: {e}")
