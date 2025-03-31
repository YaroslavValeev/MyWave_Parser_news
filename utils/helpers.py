import asyncio
import logging
import re
import time
import random
from typing import List, Optional
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient, types
import telethon
from telethon.errors import FloodWaitError
from config.settings import config

logger = logging.getLogger(__name__)
logger.info(f"Telethon version: {telethon.__version__}")  # Проверка версии Telethon


class RateLimiter:
    """Класс для ограничения частоты запросов."""
    def __init__(self, requests_per_minute=30):
        self.requests_per_minute = requests_per_minute
        self.last_request_time = 0
        self.delay = 60 / requests_per_minute

    async def wait(self):
        """Ожидание перед следующим запросом."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed + random.uniform(0, 1)) # Добавлена случайная задержка
        self.last_request_time = time.time()


async def safe_send_message(client, chat_id, message, rate_limiter):
    """Безопасная отправка сообщения с учетом лимитов."""
    await rate_limiter.wait()
    try:
        await client.send_message(chat_id, message)
        return True
    except FloodWaitError as e:
        logger.warning(f"Flood wait: {e.seconds} seconds")
        await asyncio.sleep(e.seconds + random.uniform(1, 3))
        return False
    except Exception as e:
        logger.error(f"Error sending message: {e}", exc_info=True)
        return False


def clean_text(text: str) -> str:
    """Очищает текст от лишних пробелов и специальных символов."""
    return ' '.join(text.strip().split())


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Извлекает video_id из ссылки на YouTube."""
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        if "v" in query_params:
            return query_params["v"][0]
        # Поддержка коротких ссылок, /shorts, /embed
        if parsed_url.netloc in ("youtu.be", "youtube.com", "www.youtube.com"):
            return parsed_url.path.split("/")[-1]
        return None
    except Exception as e:
        logger.error(f"Ошибка при извлечении ID видео: {e}", exc_info=True)
        return None


def normalize_source_name(url: str) -> str:
    """Возвращает нормализованное имя источника (доменное имя)."""
    parsed_url = urlparse(url)
    return parsed_url.netloc.replace("www.", "")


def extract_emails(text: str) -> List[str]:
    """Извлекает email-адреса из текста."""
    return re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)


def extract_phones(text: str) -> List[str]:
    """Извлекает телефонные номера из текста."""
    return re.findall(r'\+?\d{1,3}[-.\s]?\(?\d{2,3}\)?[-.\s]?\d{3}[-.\s]?\d{2,4}', text)


def extract_usernames(text: str) -> List[str]:
    """Извлекает Telegram usernames из текста."""
    return re.findall(r'@[A-Za-z0-9_]+', text)


async def download_media(message: types.Message, download_dir: str = "downloads/") -> bool:
    """Загружает медиа из сообщения Telegram."""
    try:
        if message.media:
            file_ext = ""
            if isinstance(message.media, types.MessageMediaPhoto):
                file_ext = ".jpg"
            elif isinstance(message.media, types.MessageMediaVideo):
                file_ext = ".mp4"
            # ... добавить другие типы медиа ...
            await message.download_media(file=f"{download_dir}{message.id}{file_ext}")
            await asyncio.sleep(config.MEDIA_DOWNLOAD_DELAY)
        return True
    except Exception as e:
        if "cancel" in str(e).lower():
            logger.error(f"Ошибка загрузки медиа для сообщения ID {message.id}: {e}", exc_info=True)
            return False
        return False
