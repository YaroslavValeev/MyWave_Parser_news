import pytest
from unittest.mock import AsyncMock, MagicMock
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

    try:
        results = parse_rss(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("title" in item and "content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_rss: {e}")

def test_parse_youtube():
    """Тест парсинга YouTube-канала."""
    source = NewsSource("youtube", "https://www.youtube.com/channel/UCJluNGyCBXAR6-CHPRMrZUw", True, None, "JB O'Neill")

    try:
        results = parse_youtube(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("title" in item and "content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_youtube: {e}")

def test_parse_website():
    """Тест парсинга веб-сайтов."""
    source = NewsSource("website", "https://www.wakeboardingmag.com", True, None, "Wakeboarding Magazine")

    try:
        results = parse_website(source, ["wake", "surf"])
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("title" in item and "content" in item for item in results)
    except Exception as e:
        pytest.fail(f"Ошибка в parse_website: {e}")
