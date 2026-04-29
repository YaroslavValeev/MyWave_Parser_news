import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from bot import start_command, parse_command
from config.settings import config

# Мок-классы для Telegram API
class MockUser:
    """Мок пользователя Telegram."""
    def __init__(self, user_id):
        self.id = user_id
        self.first_name = "TestUser"
        self.is_bot = False

class MockMessage:
    """Мок сообщения Telegram."""
    def __init__(self, user_id, text):
        self.message_id = 1
        self.from_user = MockUser(user_id)
        self.text = text

    async def reply_text(self, text):
        self.mock_reply_text = text

class MockUpdate:
    """Мок обновления Telegram."""
    def __init__(self, user_id, message_text=""):
        self.update_id = 1
        self.message = MockMessage(user_id, message_text)

# Тесты команд
@pytest.mark.asyncio
async def test_start_command_success():
    """Тест команды /start при успешном выполнении."""
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()

    with patch('bot.TelegramSessionManager.get_client', return_value=AsyncMock()):
        await start_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Здравствуйте! Я бот MyWave_Parser_WakeNews" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_authorized(monkeypatch):
    """Тест команды /parse для авторизованного пользователя."""
    monkeypatch.setattr(config, "ADMIN_ID", 12345, raising=False)
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()
    
    mock_data = [{"id": "1", "title": "Test News"}]
    with patch('bot.TelegramParser.parse', return_value=mock_data), \
         patch('bot.save_to_google_sheets', return_value=None):
        await parse_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Парсинг успешно завершён" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_unauthorized():
    """Тест команды /parse для неавторизованного пользователя."""
    update = MockUpdate(user_id=54321)  # Не админ
    context = SimpleNamespace()
    
    await parse_command(update, context)
    assert hasattr(update.message, "mock_reply_text")
    assert "Вы не авторизованы для выполнения этой команды" in update.message.mock_reply_text

# Интеграционные тесты
@pytest.mark.asyncio
async def test_integration_parsing_and_saving(monkeypatch):
    """Интеграционный тест парсинга и сохранения данных."""
    monkeypatch.setattr(config, "ADMIN_ID", 12345, raising=False)
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()
    
    mock_data = [{"id": "1", "title": "Test News", "source_type": "telegram"}]
    with patch('bot.TelegramParser.parse', return_value=mock_data), \
         patch('bot.init_google_sheets', return_value={'news': AsyncMock()}), \
         patch('bot.save_to_google_sheets', return_value=None) as mock_save:
        await parse_command(update, context)
        mock_save.assert_called_once()
        assert "Парсинг успешно завершён" in update.message.mock_reply_text

# Тесты на ошибки
@pytest.mark.asyncio
async def test_start_command_telegram_error():
    """Тест команды /start при ошибке подключения к Telegram."""
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()
    
    with patch('bot.TelegramSessionManager.get_client', side_effect=Exception("Telegram connection failed")):
        await start_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка инициализации" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_parsing_error(monkeypatch):
    """Тест команды /parse при ошибке парсинга."""
    monkeypatch.setattr(config, "ADMIN_ID", 12345, raising=False)
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()
    
    with patch('bot.TelegramParser.parse', side_effect=Exception("Parsing failed")):
        await parse_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка парсинга" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_google_sheets_error(monkeypatch):
    """Тест команды /parse при ошибке Google Sheets."""
    monkeypatch.setattr(config, "ADMIN_ID", 12345, raising=False)
    update = MockUpdate(user_id=12345)
    context = SimpleNamespace()
    
    mock_data = [{"id": "1", "title": "Test News", "source_type": "telegram"}]
    with patch('bot.TelegramParser.parse', return_value=mock_data), \
         patch('bot.init_google_sheets', side_effect=Exception("Google Sheets error")):
        await parse_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка Google Sheets" in update.message.mock_reply_text
