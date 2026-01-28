import asyncio
import logging
import signal
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List
from aiohttp import ClientError
import requests
from bs4 import BeautifulSoup
import feedparser
from abc import ABC, abstractmethod
import tenacity
from urllib.parse import urlparse
from dotenv import load_dotenv
import hashlib
from telethon import TelegramClient, types
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    SessionPasswordNeededError,
    RpcCallFailError,  # Используйте это вместо ConnectionError
    TimedOutError
)
from telethon.network import ConnectionTcpFull 
from google.oauth2.service_account import Credentials
import gspread
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from telethon.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage, MessageMediaPoll
from utils.telegram_session import TelegramSessionManager#  
# Imports from local modules (ensure these are available in your project)
from utils.helpers import clean_text, extract_youtube_video_id
from storage.sources import list_sources
from config.settings import config
from collectors.telegram_parser import download_media
from telegram_rate_limiter import safe_send_message, RateLimiter
from utils.telegram_session import TelegramSessionManager
# P0: "utils/import asyncio.py" (с пробелом) импортируется через shim utils/import_asyncio.py
from utils import import_asyncio as import_asyncio
from utils.row_utils import generate_checksum, validate_raw_row
from models import NewsItem
import hashlib

# Load environment variables from .env file
load_dotenv()

# Улучшенная настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("parser.log", mode='w'),
        logging.StreamHandler()
    ],
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Константы для безопасного парсинга
TELEGRAM_LIMITS = {
    'get_entity': 3/60,
    'iter_messages': 1/3
}

class SafeParser:
    """
    Вспомогательный класс для безопасного парсинга.
    """
    @staticmethod
    async def human_delay():
        """Вносит случайную задержку для имитации действий человека."""
        delay = random.uniform(1.5, 4.0)
        logger.debug(f"Задержка: {delay:.2f} сек")
        await asyncio.sleep(delay)

    @staticmethod
    def is_blocked(response):
        """Проверяет, заблокирован ли запрос."""
        return response.status_code == 429 or "captcha" in response.text

# Добавляем абстрактный класс BaseParser
class BaseParser(ABC):
    """
    Абстрактный базовый класс для всех парсеров.
    """
    def __init__(self, limit=50):
        """
        Инициализирует BaseParser.

        Args:
            limit (int): Максимальное количество элементов для парсинга.
        """
        self.limit = limit

    @abstractmethod
    async def parse(self, source):
        """
        Абстрактный метод для парсинга данных из источника.

        Args:
            source: Источник данных.

        Returns:
            List[Dict]: Список словарей с данными.
        """
        pass

    def validate_source(self, source):
        """
        Проверяет корректность источника перед парсингом.

        Args:
            source: Источник данных.

        Raises:
            ValueError: Если источник некорректен.
        """
        if not hasattr(source, 'url') or not source.url:
            raise ValueError(f"Некорректный источник: {source}")
        
        # Дополнительная проверка URL
        parsed_url = urlparse(source.url)
        if not all([parsed_url.scheme, parsed_url.netloc]):
            raise ValueError(f"Некорректный URL: {source.url}")
        return True

async def check_session(client: TelegramClient) -> bool:
    """Check if the Telegram client session is valid."""
    try:
        if not await client.is_user_authorized():
            logger.error("Session is not authorized")
            raise AuthKeyUnregisteredError("Session not authorized")
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки сессии: {e}")
        raise
async def init_google_sheets():
    """Инициализация Google Sheets с обработкой ошибок и поддержкой всех листов в нижнем регистре с подчёркиванием"""
    try:
        # P0: используем каноничную реализацию Google Sheets из utils/import asyncio.py (через shim).
        # Вся запись в raw_feed должна быть header-based, без позиционных "коротких" списков.
        doc = await import_asyncio.init_google_sheets()
        if not doc:
            return None

        # P1/self-healing (безопасно): добавляем отсутствующие колонки из схемы, если таблица расширилась.
        # Для raw_feed схема синхронизирована с фактической таблицей (68 колонок).
        await import_asyncio.ensure_sheet_headers(doc, sheet_name="raw_feed")

        logger.info("Google Sheets initialized (каноничная реализация + schema-aligned заголовки)")
        return doc
    except Exception as e:
        logger.error(f"Ошибка Google Sheets: {e}")
        return None

def generate_unique_id():
    """Генерация уникального идентификатора."""
    return str(uuid.uuid4())

async def get_author_info(client, message) -> Dict:
    """Получаем информацию об авторе из комментариев"""
    try:
        if not message.replies:
            return {}
            
        comments = await client.get_messages(
            message.peer_id,
            reply_to=message.id,
            limit=10
        )
        
        for comment in comments:
            if comment.sender:
                return {
                    'user_id': comment.sender.id,
                    'telegram_name': getattr(comment.sender, 'username', '')
                }
    except Exception:
        return {}
    return {}

def classify_telegram_content(text: str) -> str:
    """Классификация типа контента"""
    text = text.lower()
    keywords = {
        'новости': ['новость', 'анонс', 'событие', 'мероприятие'],
        'реклама': ['акция', 'скидка', 'промокод', 'реклама'],
        'обучение': ['урок', 'обучение', 'совет', 'инструкция'],
        'мнение': ['мнение', 'комментарий', 'эксперт', 'точка зрения']
    }
    
    for content_type, words in keywords.items():
        if any(word in text for word in words):
            return content_type
    return 'сообщение'

def get_rss_date(entry) -> str:
    """Получаем дату из RSS записи"""
    for date_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
        if hasattr(entry, date_field):
            return datetime(*getattr(entry, date_field)[:6]).isoformat()
    return datetime.now().isoformat()

async def process_message(client, message):
    """Обработка одного сообщения с проверками и обработкой ошибок"""
    try:
        if isinstance(message, bool):
            logger.warning(f"Получено булево значение вместо сообщения: {message}")
            return None

        unique_id = generate_unique_id()
        text = message.text or ""
        media_path = None
        media_type = None
        
        if message.date:
            msg_date = message.date.isoformat()
        else:
            msg_date = datetime.now(timezone.utc).isoformat()
        
        media_url = ''
        if message.media:
            try:
                # Try to extract a public URL from the message (webpage preview, etc.)
                if hasattr(message, 'web_page') and hasattr(message.web_page, 'url'):
                    media_url = message.web_page.url
                # Optionally, check for other public media URLs (e.g., message.photo with external URL)
                # If you want to support Telegram CDN links, you can try to get them here
                # Otherwise, do not save local file path
            except Exception as e:
                logger.error(f"Ошибка при обработке медиа: {e}")

        msg_data = {
            'id': unique_id,
            'source_type': 'telegram',
            'source_name': message.peer_id.title if hasattr(message.peer_id, 'title') else '',
            'source_url': f"https://t.me/{message.peer_id.username}" if hasattr(message.peer_id, 'username') and message.peer_id.username else '',
            'created_at': msg_date,
            'raw_title': clean_text(text)[:100] if text else '',  # Первые 100 символов текста
            'raw_content': clean_text(text),
            'raw_html': '',  # В Telegram нет HTML
            'raw_media': media_url if media_url and str(media_url).startswith('http') else '',  # Только публичная ссылка
            'lang': 'ru',
            'raw_tags': '',
            'ingest_status': '',  # Оставляем пустым как требуется
            'need_opinion': 'false',
            'expert_opinion': '',
            'published_posts': '',
            'parse_error': '',
            'updated_at': msg_date,
            'checksum': '',
            'debug_info': f"media_type: {media_type}" if 'media_type' in locals() and media_type else '',
            'review_queue': 'false'
        }
        return msg_data
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения {message.id if hasattr(message, 'id') else 'unknown'}: {str(e)}")
        return None

# Создаем класс TelegramParser, наследуясь от BaseParser
class TelegramParser(BaseParser):
    def __init__(self, client, limit=50):
        super().__init__(limit)
        self.client = client
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 10  # seconds

    async def safe_reconnect(self):
        """Безопасное переподключение с экспоненциальной задержкой"""
        for attempt in range(self.max_reconnect_attempts):
            try:
                await self.client.disconnect()
                await self.client.connect()
                if await self.client.is_user_authorized():
                    logger.info("Успешное переподключение")
                    return True
            except Exception as e:
                delay = self.reconnect_delay * (2 ** attempt)
                logger.error(f"Попытка {attempt + 1}/{self.max_reconnect_attempts}: Ошибка подключения: {e}")
                await asyncio.sleep(delay)
        return False

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        retry=tenacity.retry_if_exception_type((RpcCallFailError, TimeoutError)),
        reraise=True
    )
    async def parse(self, source):
        self.validate_source(source)
        try:
            if not self.client.is_connected() and not await self.safe_reconnect():
                return []

            entity = await self.client.get_entity(source.url)
            logger.info(f"Канал найден: {entity.title}")

            messages = []
            async for message in self.client.iter_messages(entity, limit=self.limit):
                try:
                    await SafeParser.human_delay()
                    msg_data = await process_message(self.client, message)
                    if msg_data:
                        messages.append(msg_data)
                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения: {e}")

            return messages

        except (RpcCallFailError, TimeoutError) as e:
            logger.error(f"Критическая ошибка соединения: {str(e)}")
            return []
        except ValueError as e:
            logger.warning(f"Канал {source.url} не найден: {e}")
            return []
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return []

# Создаем класс RSSParser, наследуясь от BaseParser
class RSSParser(BaseParser):
    """
    Парсер для RSS-лент.
    """
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """
        Парсит RSS-ленту.

        Args:
            source: Источник данных (объект с атрибутом url).

        Returns:
            List[Dict]: Список словарей с данными из RSS-ленты.
        """
        self.validate_source(source)
        try:
            feed = feedparser.parse(source.url)
            items = []
            
            for entry in feed.entries[:self.limit]:
                raw_html = entry.get('content', [{'value': ''}])[0]['value'] if 'content' in entry else ""
                tags = ','.join(t.term for t in entry.tags) if hasattr(entry, "tags") else ""
                created_at = get_rss_date(entry)
                # Очищаем HTML для raw_content
                plain_text = clean_text(BeautifulSoup(raw_html, "html.parser").get_text()) if raw_html else clean_text(entry.get('description', ''))
                items.append({
                    'id': generate_unique_id(),
                    'source_type': 'rss',
                    'source_name': source.name,
                    'source_url': source.url,
                    'created_at': created_at,
                    'raw_title': clean_text(entry.get('title', '')),
                    'raw_content': plain_text,
                    'raw_html': raw_html,
                    'raw_media': '',
                    'lang': 'ru',
                    'raw_tags': tags,
                    'ingest_status': '',
                    'need_opinion': 'false',
                    'expert_opinion': '',
                    'published_posts': '',
                    'parse_error': '',
                    'updated_at': created_at,
                    'checksum': '',
                    'debug_info': '',
                    'review_queue': 'false'
                })
                
            return items
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS: {e}")
            return []

# Создаем класс WebsiteParser, наследуясь от BaseParser
class WebsiteParser(BaseParser):
    """
    Парсер для веб-сайтов.
    """
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """
        Парсит веб-сайт.

        Args:
            source: Источник данных (объект с атрибутом url).

        Returns:
            List[Dict]: Список словарей с данными с веб-сайта.
        """
        self.validate_source(source)
        try:
            response = requests.get(source.url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            articles = []
            for article in soup.select('article')[:self.limit]:
                title_elem = article.find('h2')
                title = title_elem.get_text(strip=True) if title_elem else ''
                content_elem = article.find('div', class_='entry-content')
                # Очищаем HTML для raw_content
                if content_elem:
                    plain_text = clean_text(content_elem.get_text())
                else:
                    plain_text = ''
                images = []
                for img in article.find_all('img'):
                    if 'src' in img.attrs:
                        images.append(img['src'])
                created_at = datetime.now(timezone.utc).isoformat()
                if title or plain_text:
                    articles.append({
                        'id': generate_unique_id(),
                        'source_type': 'website',
                        'source_name': source.name,
                        'source_url': source.url,
                        'created_at': created_at,
                        'raw_title': clean_text(title),
                        'raw_content': plain_text,
                        'raw_html': str(article),
                        'raw_media': '\n'.join(images),
                        'lang': 'ru',
                        'raw_tags': '',
                        'ingest_status': '',
                        'need_opinion': 'false',
                        'expert_opinion': '',
                        'published_posts': '',
                        'parse_error': '',
                        'updated_at': created_at,
                        'checksum': '',
                        'debug_info': '',
                        'review_queue': 'false'
                    })
                    
            return articles
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к сайту {source.url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Ошибка парсинга сайта: {e}")
            return []

# Создаем класс YoutubeParser, наследуясь от BaseParser
class YoutubeParser(BaseParser):
    """
    Парсер для YouTube-каналов.
    """
    def __init__(self, api_key, limit=50):
        """
        Инициализирует YoutubeParser.

        Args:
            api_key (str): Ключ API YouTube.
            limit (int): Максимальное количество видео для парсинга.
        """
        super().__init__(limit)
        self.api_key = api_key
        self.youtube = build('youtube', 'v3', developerKey=self.api_key)

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """
        Парсит YouTube-канал.

        Args:
            source: Источник данных (объект с атрибутом channel_id).

        Returns:
            List[Dict]: Список словарей с данными о видео.
        """
        self.validate_source(source)
        if not hasattr(source, 'channel_id') or not source.channel_id:
            raise ValueError(f"Некорректный источник YouTube: {source}")
        try:
            request = self.youtube.search().list(
                part="snippet",
                channelId=source.channel_id,
                maxResults=self.limit
            )
            response = request.execute()
            items = []
            for item in response['items']:
                video_id = item['id']['videoId']
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                
                # Попытка получить транскрипт
                try:
                    transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    transcript_text = ' '.join([t['text'] for t in transcript])
                except Exception as e:
                    logger.warning(f"Не удалось получить транскрипт для видео {video_id}: {e}")
                    transcript_text = ""
                
                # Получаем thumbnail как медиа
                thumbnails = item['snippet'].get('thumbnails', {})
                media_url = ''
                for quality in ['maxres', 'high', 'medium', 'default']:
                    if quality in thumbnails:
                        media_url = thumbnails[quality]['url']
                        break
                
                created_at = item['snippet']['publishedAt']
                
                items.append({
                    'id': generate_unique_id(),
                    'source_type': 'youtube',
                    'source_name': source.name,
                    'source_url': video_url,
                    'created_at': created_at,
                    'raw_title': clean_text(item['snippet']['title']),
                    'raw_content': clean_text(item['snippet']['description'] + "\n\n" + transcript_text),
                    'raw_html': '',  # YouTube не предоставляет HTML
                    'raw_media': media_url,  # URL превью видео
                    'lang': item['snippet'].get('defaultLanguage', 'ru'),  # Язык из API или по умолчанию
                    'raw_tags': '',  # Теги пока не извлекаем
                    'ingest_status': '',  # Исправлено: теперь всегда пустое значение
                    'need_opinion': 'false',
                    'expert_opinion': '',
                    'published_posts': '',
                    'parse_error': '',
                    'updated_at': created_at,
                    'checksum': '',  # TODO: Добавить вычисление контрольной суммы
                    'debug_info': f"video_id: {video_id}",
                    'review_queue': 'false'
                })
            return items
        except Exception as e:
            logger.error(f"YouTube parsing error: {e}")
            return []

# === UNIVERSAL LINK HANDLER & BOT MESSAGE HANDLER ===
import re
from types import SimpleNamespace

async def ask_gpt(text: str) -> str:
    """Заглушка для анализа текста через GPT. Замените на реальную интеграцию."""
    # TODO: Реализовать реальный вызов GPT
    return f"[GPT-анализ]: {text[:200]}..."

def detect_link_type(url: str) -> str:
    """Определяет тип ссылки: telegram, youtube, rss, website."""
    if re.match(r"https://t\.me/|tg://", url):
        return "telegram"
    if re.match(r"https://(www\.)?youtube\.com|https://youtu\.be", url):
        return "youtube"
    if url.endswith('.xml') or 'rss' in url:
        return "rss"
    if re.match(r"https?://", url):
        return "website"
    return "unknown"

async def parse_telegram(url, client):
    """Парсинг Telegram-ссылки (только 2 последних сообщения)."""
    parser = TelegramParser(client, limit=2)
    source = SimpleNamespace(url=url, name=url, type='telegram')
    return await parser.parse(source)

async def parse_youtube(url, api_key):
    """Парсинг YouTube-ссылок (2 последних видео)."""
    # Извлекаем channel_id из ссылки (заглушка)
    channel_id = extract_youtube_video_id(url) or url  # Используйте свою функцию
    parser = YoutubeParser(api_key, limit=2)
    source = SimpleNamespace(channel_id=channel_id, url=url, name=url, type='youtube')
    return await parser.parse(source)

async def parse_website(url):
    """Парсинг сайта (2 последних статьи)."""
    parser = WebsiteParser(limit=2)
    source = SimpleNamespace(url=url, name=url, type='website')
    return await parser.parse(source)

async def parse_rss(url):
    """Парсинг RSS-ленты (2 последних новости)."""
    parser = RSSParser(limit=2)
    source = SimpleNamespace(url=url, name=url, type='rss')
    return await parser.parse(source)

def generate_checksum(row):
    """
    Устаревшая функция - используйте utils.row_utils.generate_checksum вместо этого.
    Теперь генерирует checksum на основе содержимого (raw_title + raw_content + raw_html).
    """
    from utils.row_utils import generate_checksum as generate_checksum_new
    return generate_checksum_new(row)

def validate_raw_row(row: dict) -> bool:
    """Валидирует обязательные поля для записи в raw_feed."""
    required = ['id', 'source_url', 'raw_content', 'checksum']
    for key in required:
        if not row.get(key):
            logger.error(f"Отклонена запись: отсутствует обязательное поле {key} — {row}")
            return False
    return True

async def handle_user_link(url, client, api_key, doc):
    """Универсальный обработчик пользовательских ссылок."""
    logger.info(f"Получена ссылка от пользователя: {url}")
    link_type = detect_link_type(url)
    logger.info(f"Тип ссылки определён как: {link_type}")
    try:
        if link_type == "telegram":
            data = await parse_telegram(url, client)
        elif link_type == "youtube":
            data = await parse_youtube(url, api_key)
        elif link_type == "rss":
            data = await parse_rss(url)
        elif link_type == "website":
            data = await parse_website(url)
        else:
            data = []
        return data
    except Exception as e:
        logger.error(f"Ошибка при обработке ссылки: {e}", exc_info=True)
        return []
    
async def save_to_google_sheets(doc, data):
    """
    P0: Сохранение данных в Google Sheets (raw_feed) — ТОЛЬКО header-based запись.
    Запрещены позиционные "короткие" записи на 14–15 полей.

    Вставка новых строк должна гарантировать заполнение row_number (fail-fast если не получилось).
    """
    try:
        if not doc:
            doc = await import_asyncio.init_google_sheets()
        if not doc:
            return

        rows: List[Dict] = []
        for item in data:
            try:
                row = {
                    "id": str(item.get("id") or ""),
                    "source_type": item.get("source_type", ""),
                    "source_name": item.get("source_name", ""),
                    "source_url": item.get("source_url", ""),
                    "raw_title": item.get("raw_title", item.get("title", "")),
                    "raw_content": item.get("raw_content", item.get("text", "")),
                    "raw_html": item.get("raw_html", ""),
                    "raw_media": item.get("raw_media", ""),
                    "lang": item.get("lang", ""),
                    "raw_tags": item.get("raw_tags", ""),
                    "ingest_status": "ok",
                    "status": "DRAFT",
                }
                # checksum обязателен и используется для дедупликации
                row["checksum"] = item.get("checksum") or generate_checksum(row)
                rows.append(row)
            except Exception as e:
                logger.error(f"Ошибка подготовки строки для Sheets: {e}")

        if rows:
            await import_asyncio.save_to_sheet(doc, "raw_feed", rows)
    except Exception as e:
        logger.error(f"Ошибка при сохранении в Google Sheets (raw_feed): {e}", exc_info=True)

def handle_signals():
    """Обработчик сигналов"""
    def shutdown(signum, frame):
        logger.info("Завершение работы...")
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

MEDIA_HANDLERS = {
    MessageMediaPhoto: lambda m: m.photo,
    MessageMediaDocument: lambda m: m.document,
    MessageMediaWebPage: lambda m: None,
    MessageMediaPoll: lambda m: None
}

async def download_media(client, message):
    """Скачивание медиа с повторными попытками"""
    if not hasattr(message, 'media') or not message.media:
        return None, None
    try:
        # Определяем тип медиа
        if isinstance(message.media, MessageMediaPhoto):
            media_type = "photo"
            file = await client.download_media(
                message.media,
                file=os.path.join("media", f"{uuid.uuid4()}.jpg")
            )
        elif isinstance(message.media, MessageMediaDocument):
            media_type = "video"
            doc = message.media.document
            ext = doc.mime_type.split('/')[-1] if doc.mime_type else 'unknown'
            file = await client.download_media(
                doc,
                file=os.path.join("media", f"{uuid.uuid4()}.{ext}")
            )
        else:
            # Неизвестный тип медиа
            return None, None
        return file, media_type
    except Exception as e:
        logger.error(f"Ошибка при скачивании медиа: {e}")
        return None, None
    except Exception as e:
        logger.warning(f"Неизвестный тип ссылки: {url}")
        return "Не удалось определить тип ссылки. Поддерживаются: Telegram, YouTube, RSS, сайты."
    
        if not data:
            return "Не удалось получить данные по ссылке."
        # Анализируем через GPT каждую новость
        for item in data:
            item['expert_opinion'] = await ask_gpt(item.get('raw_content', ''))
            # Генерируем checksum и id, валидируем
            if 'checksum' not in item or not item['checksum']:
                item['checksum'] = generate_checksum(item)
            if 'id' not in item or not item['id']:
                item['id'] = item['checksum']
        # Сохраняем в Google Sheets (raw_feed) только валидные строки
        all_data = {k: [] for k in import_asyncio.SHEET_COLUMNS.keys()}
        all_data['raw_feed'].extend([item for item in data if validate_raw_row(item)])
        await import_asyncio.auto_save_to_sheets(doc, all_data)
        logger.info(f"Ссылка обработана и сохранена: {url}")
        return f"Ссылка успешно обработана и сохранена: {url}"
    except Exception as e:
        logger.error(f"Ошибка при обработке ссылки: {e}", exc_info=True)
        return f"Ошибка при обработке ссылки: {e}"

# === Пример обработчика сообщений Telegram-бота (aiogram/Telethon) ===
# Ниже пример для aiogram. Для Telethon используйте аналогичный подход.
# from aiogram import Bot, Dispatcher, types as aio_types
# bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
# dp = Dispatcher(bot)
#
# @dp.message_handler(lambda message: message.text and re.match(r'https?://', message.text))
# async def link_handler(message: aio_types.Message):
#     status = await handle_user_link(message.text, client, config.YOUTUBE_API_KEY, doc)
#     await message.reply(status)

async def main():
    import_asyncio.handle_signals()
    logger.info("Инициализация Google Sheets...")
    doc = await import_asyncio.init_google_sheets()
    if not doc:
        logger.error("Не удалось инициализировать Google Sheets")
        return

    logger.info("Инициализация бота...")

    # Проверка обязательных настроек
    required_vars = [
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        config.TELEGRAM_PHONE,
        config.GOOGLE_CREDENTIALS_FILE,
        config.GOOGLE_SHEET_ID,
        config.YOUTUBE_API_KEY
    ]

    if not all(required_vars):
        logger.error("Отсутствуют обязательные настройки в .env файле")
        return

    # Настройка прокси
    proxy_config = None
    if config.PROXY_ENABLED:
        if config.PROXY_USER and config.PROXY_PASS:
            proxy_config = (
                config.PROXY_TYPE,
                config.PROXY_HOST,
                config.PROXY_PORT,
                True,  # Использовать SSL
                config.PROXY_USER,
                config.PROXY_PASS
            )
        else:
            proxy_config = (
                config.PROXY_TYPE,
                config.PROXY_HOST,
                config.PROXY_PORT
            )

    client = None
    try:
        client = TelegramClient(
            'session_name',
            api_id=config.TELEGRAM_API_ID_USER,
            api_hash=config.TELEGRAM_API_HASH_USER,
            connection=ConnectionTcpFull,
            connection_retries=10,
            auto_reconnect=True,
            proxy=proxy_config,
            request_retries=5,
            flood_sleep_threshold=120,
            device_model="MyDevice",
            system_version="10",
            app_version="1.0"
        )

        await client.connect()
        async with client:
            if not await client.is_user_authorized():
                await client.send_code_request(config.TELEGRAM_PHONE)
                await client.sign_in(config.TELEGRAM_PHONE, input('Введите код: '))

            session_manager = TelegramSessionManager(
                api_id=config.TELEGRAM_API_ID_USER,
                api_hash=config.TELEGRAM_API_HASH_USER,
                phone=config.TELEGRAM_PHONE
            )
            parsers = {
                "telegram": TelegramParser(client, limit=2),
                "rss": RSSParser(limit=2),
                "website": WebsiteParser(limit=2),
                "youtube": YoutubeParser(config.YOUTUBE_API_KEY, limit=2)
            }

            while True:
                logger.info("Старт новой итерации парсинга...")
                all_data = {k: [] for k in import_asyncio.SHEET_COLUMNS.keys()}
                tasks = []
                for source in list_sources():
                    parser = parsers.get(source.type)
                    if parser:
                        tasks.append(parser.parse(source))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Ошибка при парсинге: {result}", exc_info=True)
                    elif result:                        # Все новости только в raw_feed
                        if isinstance(result, list):
                            all_data['raw_feed'].extend(result)
                        elif isinstance(result, dict):
                            for k, v in result.items():
                                if k in all_data and isinstance(v, list):
                                    all_data[k].extend(v)
                await import_asyncio.auto_save_to_sheets(doc, all_data)
                logger.info(f"Автоматически сохранено: {[f'{k}: {len(v)}' for k,v in all_data.items() if v]} записей")
                logger.info("Завершена итерация парсинга. Ожидание...")
                await asyncio.sleep(getattr(config, 'PARSING_INTERVAL', 3600))

    except asyncio.CancelledError:
        logger.info("Получен сигнал отмены. Завершаем работу корректно...")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}", exc_info=True)
    finally:
        if client:
            await client.disconnect()
        # Явное закрытие Google Sheets (если требуется)
        try:
            if hasattr(doc, 'close'):
                doc.close()
        except Exception as e:
            logger.warning(f"Ошибка при закрытии Google Sheets: {e}")

# Точка интеграции с реальным Telegram-ботом (aiogram/Telethon) должна быть реализована здесь
# def start_telegram_bot():
#     pass  # TODO: интеграция с aiogram/Telethon

if __name__ == "__main__":
    logger.info("Запуск основного цикла парсинга...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
