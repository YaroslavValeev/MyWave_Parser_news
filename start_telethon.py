import logging
import asyncio
import os
from utils.telegram_session import TelegramSessionManager  # Импортируем утилиту для управления сессией
from config.settings import config  # Импорт для учетных данных

# optional dev fake client
DEV_USE_FAKE = os.getenv('DEV_USE_FAKE_TELETHON', 'False').lower() in ('1', 'true', 'yes')
if DEV_USE_FAKE:
    from utils.fake_telethon import FakeClient as _MaybeFakeClient
else:
    _MaybeFakeClient = None

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def start_telethon():
    """Инициализация и авторизация Telethon."""
    # If developer mode is enabled, return a fake client that doesn't require
    # interactive auth. Otherwise, use the real session manager.
    if DEV_USE_FAKE and _MaybeFakeClient is not None:
        client = _MaybeFakeClient()
        await client.connect()
        logger.info("✅ Dev fake Telethon-client initialized.")
        return client

    session_manager = TelegramSessionManager(
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE
    )

    try:
        client = await session_manager.get_client()
        if client:
            logger.info("✅ Telethon-клиент успешно инициализирован.")
            return client
        else:
            logger.error("❌ Не удалось инициализировать Telethon-клиент.")
            return None
    except Exception as e:
        logger.error(f"Ошибка при запуске клиента Telegram: {e}")
        raise

async def close_telethon(client):
    """Закрытие клиента Telethon."""
    try:
        if client:
            await client.disconnect()
            logger.info("✅ Telethon-клиент успешно отключен.")
    except Exception as e:
        logger.error(f"Ошибка при отключении клиента Telegram: {e}")

if __name__ == "__main__":
    async def main():
        client = await start_telethon()
        if client:
            # Здесь можно добавить код для работы с клиентом
            # Например, await client.send_message('me', 'Hello!')
            pass
        await close_telethon(client)

    asyncio.run(main())
