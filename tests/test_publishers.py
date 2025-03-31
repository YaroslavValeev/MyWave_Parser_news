import pytest
from unittest.mock import MagicMock, patch
from publishers.telegram_publisher import TelegramPublisher

@pytest.fixture
def mock_publisher():
    """Создаёт тестовый объект TelegramPublisher с мок-ботом."""
    with patch("publishers.telegram_publisher.Bot") as MockBot:
        bot_instance = MockBot.return_value
        publisher = TelegramPublisher("test-bot-token", "@test_channel")
        publisher.bot = bot_instance
        yield publisher

def test_send_text_message(mock_publisher):
    """Тест отправки текстового сообщения."""
    news_item = {"title": "Тест", "content": "Тестовое сообщение", "link": "https://example.com"}
    mock_publisher.send_news(news_item)
    mock_publisher.bot.send_message.assert_called_once()

def test_send_media(mock_publisher):
    """Тест отправки медиа (изображения)."""
    news_item = {
        "title": "Медиа тест",
        "content": "Проверка отправки изображения",
        "images": ["https://example.com/test.jpg"]
    }
    mock_publisher.send_news(news_item)
    mock_publisher.bot.send_media_group.assert_called_once()

def test_handle_telegram_error(mock_publisher):
    """Тест обработки ошибки Telegram API."""
    news_item = {"title": "Ошибка тест", "content": "Это тест ошибки"}
    mock_publisher.bot.send_message.side_effect = Exception("Ошибка Telegram")
    
    with patch("logging.Logger.error") as mock_logger:
        mock_publisher.send_news(news_item)
        mock_logger.assert_called_once()
