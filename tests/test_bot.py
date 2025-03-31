import pytest
from unittest.mock import AsyncMock, patch
from telegram import Update, Message, User
from telegram.ext import CallbackContext
from bot import start_command, parse_command, main  # Предполагаем, что main — основной цикл
from config.settings import config

# Мок-классы для Telegram API
class MockUser(User):
    """Мок пользователя Telegram."""
    def __init__(self, user_id):
        super().__init__(id=user_id, first_name="TestUser", is_bot=False)

class MockMessage(Message):
    """Мок сообщения Telegram."""
    def __init__(self, user_id, text):
        super().__init__(message_id=1, date=None, chat=None, from_user=MockUser(user_id))
        self.text = text

    async def reply_text(self, text):
        self.mock_reply_text = text

class MockUpdate(Update):
    """Мок обновления Telegram."""
    def __init__(self, user_id, message_text=""):
        super().__init__(update_id=1)
        self.message = MockMessage(user_id, message_text)

# Тесты команд
@pytest.mark.asyncio
async def test_start_command_success():
    """Тест команды /start при успешном выполнении."""
    update = MockUpdate(user_id=12345)
    context = AsyncMock(CallbackContext)

    with patch('bot.TelegramSessionManager.get_client', return_value=AsyncMock()):
        await start_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Здравствуйте! Я бот MyWave_Parser_WakeNews" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_authorized():
    """Тест команды /parse для авторизованного пользователя."""
    update = MockUpdate(user_id=config.ADMIN_ID)  # Предполагаем, что ADMIN_ID определён в config
    context = AsyncMock(CallbackContext)
    
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
    context = AsyncMock(CallbackContext)
    
    await parse_command(update, context)
    assert hasattr(update.message, "mock_reply_text")
    assert "Вы не авторизованы для выполнения этой команды" in update.message.mock_reply_text

# Интеграционные тесты
@pytest.mark.asyncio
async def test_integration_parsing_and_saving():
    """Интеграционный тест парсинга и сохранения данных."""
    update = MockUpdate(user_id=config.ADMIN_ID)
    context = AsyncMock(CallbackContext)
    
    mock_data = [{"id": "1", "title": "Test News", "source_type": "telegram"}]
    with patch('bot.TelegramParser.parse', return_value=mock_data), \
         patch('bot.init_google_sheets', return_value={'news': AsyncMock()}), \
         patch('bot.save_to_google_sheets', return_value=None) as mock_save:
        await parse_command(update, context)
        mock_save.assert_called_once_with({'news': AsyncMock()}, mock_data)
        assert "Парсинг успешно завершён" in update.message.mock_reply_text

# Тесты на ошибки
@pytest.mark.asyncio
async def test_start_command_telegram_error():
    """Тест команды /start при ошибке подключения к Telegram."""
    update = MockUpdate(user_id=12345)
    context = AsyncMock(CallbackContext)
    
    with patch('bot.TelegramSessionManager.get_client', side_effect=Exception("Telegram connection failed")):
        await start_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка инициализации" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_parsing_error():
    """Тест команды /parse при ошибке парсинга."""
    update = MockUpdate(user_id=config.ADMIN_ID)
    context = AsyncMock(CallbackContext)
    
    with patch('bot.TelegramParser.parse', side_effect=Exception("Parsing failed")):
        await parse_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка парсинга" in update.message.mock_reply_text

@pytest.mark.asyncio
async def test_parse_command_google_sheets_error():
    """Тест команды /parse при ошибке Google Sheets."""
    update = MockUpdate(user_id=config.ADMIN_ID)
    context = AsyncMock(CallbackContext)
    
    mock_data = [{"id": "1", "title": "Test News", "source_type": "telegram"}]
    with patch('bot.TelegramParser.parse', return_value=mock_data), \
         patch('bot.init_google_sheets', side_effect=Exception("Google Sheets error")):
        await parse_command(update, context)
        assert hasattr(update.message, "mock_reply_text")
        assert "Ошибка Google Sheets" in update.message.mock_reply_text
