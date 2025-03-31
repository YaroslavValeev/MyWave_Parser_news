import asyncio
import os
import logging
from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, SessionPasswordNeededError

logger = logging.getLogger(__name__)

class TelegramSessionManager:
    def __init__(self, api_id, api_hash, phone):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_file = "session_name.session"
        self.client = None

    async def get_client(self):
        if self.client is None:
            self.client = await self._create_client()
        return self.client

    async def _create_client(self):
        """Enhanced Telethon client initialization with session management."""

        # Remove an invalid session file with multiple attempts.
        if os.path.exists(self.session_file):
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    test_client = TelegramClient(
                        self.session_file,
                        self.api_id,
                        self.api_hash
                    )
                    await test_client.connect()
                    if not await test_client.is_user_authorized():
                        logger.warning("Невалидная сессия. Удаляю...")
                        await test_client.disconnect()  # Явное отключение перед удалением
                        try:
                            os.remove(self.session_file)
                            logger.info("Файл сессии успешно удален")
                            break
                        except PermissionError as pe:
                            if attempt == max_retries - 1:
                                logger.error(f"Failed to delete session file after {max_retries} attempts: {pe}")
                                return None
                            await asyncio.sleep(1)  # Wait before retrying.
                            continue
                    else:
                        await test_client.disconnect()
                        break
                except Exception as e:
                    logger.error(f"Ошибка при проверке сессии: {e}")
                    if os.path.exists(self.session_file):
                        try:
                            os.remove(self.session_file)
                        except PermissionError as pe:
                            logger.warning(f"Не удалось удалить файл сессии: {pe}")
                    break

        # Create a new Telethon client with connection retries enabled.
        client = TelegramClient(
            self.session_file,
            self.api_id,
            self.api_hash,
            connection_retries=3
        )

        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.info("Требуется авторизация...")
                try:
                    await client.start(
                        phone=self.phone,
                        code_callback=lambda: input("Введите код: ")
                    )
                except SessionPasswordNeededError:
                    logger.warning("Требуется 2FA пароль")
                    await client.start(
                        phone=self.phone,
                        password=lambda: input("Введите 2FA пароль: ")
                    )

            logger.info(f"Авторизован как: {await client.get_me()}")
            return client

        except AuthKeyUnregisteredError:
            logger.error("Недействительная сессия")
            if os.path.exists(self.session_file):
                try:
                    os.remove(self.session_file)
                except PermissionError as pe:
                    logger.warning("Не удалось удалить файл сессии: {pe}")
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
        return None

    async def close_client(self):
        if self.client:
            await self.client.disconnect()
            self.client = None
