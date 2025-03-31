import asyncio
import time
import random
import logging
from telethon.errors import FloodWaitError

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Класс для ограничения частоты запросов.
    """
    def __init__(self, requests_per_minute=10):
        """
        Инициализирует RateLimiter.

        Args:
            requests_per_minute (int): Максимальное количество запросов в минуту.
        """
        self.requests_per_minute = requests_per_minute
        self.last_request_time = 0
        self.requests_count = 0

    async def acquire(self):
        """
        Запрашивает разрешение на выполнение запроса.
        Если необходимо, ждет, чтобы не превысить лимит.
        """
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < 60 / self.requests_per_minute:
            wait_time = 60 / self.requests_per_minute - time_since_last_request + random.uniform(0, 1)
            logger.debug(f"RateLimiter: Задержка {wait_time:.2f} сек.")
            await asyncio.sleep(wait_time)

        self.last_request_time = time.time()
        self.requests_count += 1
        if self.requests_count > self.requests_per_minute:
            self.requests_count = 0
            extra_wait = 60 + random.uniform(0, 5)
            logger.debug(f"RateLimiter: Дополнительная задержка {extra_wait:.2f} сек.")
            await asyncio.sleep(extra_wait)

async def safe_send_message(client, chat_id, message, rate_limiter):
    """
    Безопасная отправка сообщения с учетом Rate Limiting и FloodWaitError.

    Args:
        client: Клиент Telegram.
        chat_id: ID чата.
        message: Текст сообщения.
        rate_limiter (RateLimiter): Экземпляр RateLimiter.
    """
    try:
        await rate_limiter.acquire()
        await client.send_message(chat_id, message)
    except FloodWaitError as e:
        logger.warning(f"FloodWaitError: ожидание {e.seconds} секунд")
        await asyncio.sleep(e.seconds)
        await safe_send_message(client, chat_id, message, rate_limiter)  # Повторная попытка
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")
