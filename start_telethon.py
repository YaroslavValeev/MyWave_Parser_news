import logging
import asyncio
from utils.telegram_session import TelegramSessionManager  # Импортируем утилиту для управления сессией
from config.settings import config  # Импорт для учетных данных

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def start_telethon():
    """Инициализация и авторизация Telethon."""
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
