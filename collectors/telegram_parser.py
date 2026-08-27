import asyncio
import logging
import random
import json
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Generator
from utils.helpers import download_media as download_media_helper  # Импортируем из utils/helpers
from telethon import TelegramClient
from config.settings import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseParser(ABC):
    """
    Абстрактный базовый класс для всех парсеров.
    """
    def __init__(self, limit: int = 50):
        """
        Инициализирует BaseParser.

        Args:
            limit (int): Максимальное количество элементов для парсинга (по умолчанию 50).
        """
        self.limit = limit

    @abstractmethod
    def parse(self, client: TelegramClient, source):
        """
        Abstract method for parsing data from a source.

        Args:
            client (TelegramClient): Клиент Telegram.
            source: Источник данных (объект с атрибутом url).

        """
        raise NotImplementedError

class TelethonParser(BaseParser):
    """
    Парсер для Telegram-каналов.
    """
    def __init__(self, limit: int = 50):
        """
        Инициализирует парсер Telegram.

        Args:
            limit (int): Максимальное количество сообщений для парсинга (по умолчанию 50).
        """
        super().__init__(limit)

    async def parse(
        self,
        client: TelegramClient,
        source,
        *,
        download_media: bool = True,
    ) -> Generator[dict, None, None]:  # type: ignore
        """
        Парсит сообщения из Telegram-канала и возвращает данные по структуре raw_feed.

        Args:
            client (TelegramClient): Клиент Telegram.
            source: Источник данных (объект с атрибутом url).
            download_media: скачивать вложения на диск (False — только метаданные/текст).

        Yields:
            dict: Словарь с данными сообщения.

        Raises:
            AttributeError: Если у источника отсутствует атрибут url.
            Exception: При ошибках парсинга или подключения.
        """
        if not hasattr(source, 'url') or not source.url:
            logger.error("Источник данных не содержит атрибут url или url пуст.")
            raise AttributeError("Источник данных должен содержать атрибут url")

        try:
            await self.human_delay()  # Случайная задержка перед началом
            entity = await client.get_entity(source.url)
            logger.info(f"Подключение к каналу {source.url} успешно выполнено.")

            async for message in client.iter_messages(entity, limit=self.limit):
                await self.human_delay()  # Задержка между сообщениями
                try:
                    text = message.text or ""
                    media_downloaded = False
                    media_links = []
                    if message.media and download_media:
                        media_downloaded = await download_media_helper(message)
                        # Здесь можно добавить путь к скачанному файлу в media_links
                        # media_links.append(путь_к_файлу)

                    title = getattr(message, 'post_author', '') or getattr(entity, 'title', '')
                    checksum = ''  # Можно реализовать md5(title+source.url)

                    yield {
                        "id": str(message.id),
                        "source_type": "telegram",
                        "source_name": getattr(entity, 'title', ''),
                        "source_url": source.url,
                        "created_at": message.date.strftime('%Y-%m-%d %H:%M:%S') if message.date else datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                        "ingest_status": "raw",
                        "raw_title": title,
                        "raw_content": text,
                        "raw_html": "",  # Можно добавить html-версию, если есть
                        "raw_media": json.dumps(media_links),
                        "raw_tags": "",  # Можно добавить теги, если есть
                        "checksum": checksum,
                        "parse_error": "",
                        "debug_info": f"msg_id={message.id}"
                    }

                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения {message.id}: {e}")
                    yield {
                        "id": str(message.id),
                        "source_type": "telegram",
                        "source_name": getattr(entity, 'title', ''),
                        "source_url": source.url,
                        "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                        "ingest_status": "error",
                        "raw_title": "",
                        "raw_content": "",
                        "raw_html": "",
                        "raw_media": "[]",
                        "raw_tags": "",
                        "checksum": "",
                        "parse_error": str(e),
                        "debug_info": f"msg_id={getattr(message, 'id', '')}"
                    }

        except Exception as e:
            logger.error(f"Ошибка парсинга {source.url}: {e}")
            yield {
                "id": "",
                "source_type": "telegram",
                "source_name": getattr(source, 'name', ''),
                "source_url": getattr(source, 'url', ''),
                "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                "ingest_status": "error",
                "raw_title": "",
                "raw_content": "",
                "raw_html": "",
                "raw_media": "[]",
                "raw_tags": "",
                "checksum": "",
                "parse_error": str(e),
                "debug_info": ""
            }

    async def human_delay(self):
        """
        Вносит случайную задержку для имитации действий человека.

        Notes:
            Задержка находится в диапазоне от 1.5 до 4.0 секунд.
        """
        delay = random.uniform(1.5, 4.0)
        logger.debug(f"Задержка: {delay:.2f} сек")
        await asyncio.sleep(delay)