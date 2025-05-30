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
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        doc = client.open_by_key(config.GOOGLE_SHEET_ID)
        worksheets = {
            'raw_feed': doc.worksheet("raw_feed"),
            'news_articles': doc.worksheet("news_articles"),
            'contacts': doc.worksheet("contacts"),
            'news_sources': doc.worksheet("news_sources"),
            'logs_reports': doc.worksheet("logs_reports"),
            'admin_actions_review': doc.worksheet("admin_actions_review"),
            'analytics_statistics': doc.worksheet("analytics_statistics"),
            'configuration_settings': doc.worksheet("configuration_settings")
        }
        # Заголовки для raw_feed
        raw_feed_headers = [
            "id", "source_type", "source_name", "source_url", "created_at", "ingest_status",
            "raw_title", "raw_content", "raw_html", "raw_media", "raw_tags",
            "checksum", "parse_error", "debug_info"
        ]
        if not worksheets['raw_feed'].get_all_values():
            worksheets['raw_feed'].append_row(raw_feed_headers)
        # Заголовки для news_articles (пример)
        news_articles_headers = [
            "article_id", "date", "title", "content", "summary", "questions", "expert_opinion",
            "topic", "language", "keywords", "media", "status", "posted_at", "channel_ids",
            "engagement_id", "updated_at", "editor"
        ]
        if not worksheets['news_articles'].get_all_values():
            worksheets['news_articles'].append_row(news_articles_headers)
        # Заголовки для остальных листов (примерные, скорректируйте по вашей структуре)
        if not worksheets['news_sources'].get_all_values():
            worksheets['news_sources'].append_row(["id", "type", "name", "url", "active", "last_id", "filter"])
        if not worksheets['logs_reports'].get_all_values():
            worksheets['logs_reports'].append_row(["id", "created_at", "level", "message", "context"])
        if not worksheets['admin_actions_review'].get_all_values():
            worksheets['admin_actions_review'].append_row(["id", "action", "user", "timestamp", "details"])
        if not worksheets['analytics_statistics'].get_all_values():
            worksheets['analytics_statistics'].append_row(["id", "date", "metric", "value", "details"])
        if not worksheets['configuration_settings'].get_all_values():
            worksheets['configuration_settings'].append_row(["key", "value", "description", "updated_at"])
        logger.info("Google Sheets initialized (все листы и заголовки приведены к нижнему регистру с подчёркиванием)")
        return worksheets
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
            
        comments = await safe_request(client.get_messages,
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
        if message.media:
            media_path, media_type = await download_media(client, message)

        author_info = await get_author_info(client, message)
        content_type = classify_telegram_content(text)

        if message.date:
            msg_date = message.date.isoformat()
        else:
            msg_date = datetime.now(timezone.utc).isoformat()

        msg_data = {
            'id': unique_id,
            'date': msg_date,
            'source_type': 'telegram',
            'source_url': f"https://t.me/{message.peer_id.username}" if hasattr(message.peer_id, 'username') and message.peer_id.username else '',
            'content_type': content_type,
            'title': message.peer_id.title if hasattr(message.peer_id, 'title') else '',
            'text': clean_text(text),
            'images': media_path if media_type == "photo" else '',
            'videos': media_path if media_type == "video" else '',
            'status': 'new',
            'author_id': author_info.get('user_id', ''),
            'author_name': author_info.get('telegram_name', '')
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

            entity = await safe_request(self.client.get_entity, source.url)
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
            
            for entry in feed.entries[:self.limit]:  # Ограничиваем количество записей
                items.append({
                    'id': generate_unique_id(),
                    'date': get_rss_date(entry),
                    'source_type': 'rss',
                    'source_url': source.url,
                    'content_type': 'новости',
                    'title': clean_text(entry.get('title', '')),
                    'text': clean_text(entry.get('description', '')),
                    'url': entry.get('link', source.url),
                    'type': 'новости',  # Для RSS предполагаем новостной тип
                    'source': source.name
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
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Пример для wakeboardingmag.com - нужно адаптировать под каждый сайт
            articles = []
            for article in soup.select('article')[:self.limit]:  # Limit to 20 entries.
                title_elem = article.find('h2')
                title = title_elem.get_text(strip=True) if title_elem else ''
                content_elem = article.find('div', class_='entry-content')
                content = content_elem.get_text(strip=True) if content_elem else ''
                
                if title or content:
                    articles.append({
                        'id': generate_unique_id(),
                        'date': datetime.now().isoformat(),
                        'source_type': 'website',
                        'source_url': source.url,
                        'content_type': 'новости',
                        'title': clean_text(title),
                        'text': clean_text(content),
                        'url': source.url,
                        'type': 'новости',
                        'source': source.name,
                        # No author info from website parsing by default.
                        'author_id': '',
                        'author_name': ''
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
                try:
                    transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    transcript_text = ' '.join([t['text'] for t in transcript])
                except Exception as e:
                    logger.warning(f"Не удалось получить транскрипт для видео {video_id}: {e}")
                    transcript_text = ""

                items.append({
                    'id': generate_unique_id(),
                    'date': item['snippet']['publishedAt'],
                    'source_type': 'youtube',
                    'source_url': video_url,
                    'content_type': 'обучение',  # Example content type
                    'title': item['snippet']['title'],
                    'text': item['snippet']['description'] + " " + transcript_text,
                    'url': video_url,
                    'type': 'обучение',
                    'source': source.name,
                    'author_id': '',
                    'author_name': ''
                })
            return items
        except Exception as e:
            logger.error(f"YouTube parsing error: {e}")
            return []

async def save_to_google_sheets(worksheets, data):
    """Сохранение данных в Google Sheets: только в raw_feed по новой структуре, с валидацией NewsItem и уникальными контактами"""
    try:
        # Подготовка данных для таблицы raw_feed
        raw_rows = []
        existing_ids = worksheets['raw_feed'].col_values(1)
        for item in data:
            # Валидация и преобразование через NewsItem
            try:
                created_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                # Генерация checksum (md5 от raw_title+source_url)
                checksum = hashlib.md5((item.get('raw_title', '') + item.get('source_url', '')).encode('utf-8')).hexdigest()
                news = NewsItem(
                    id=item.get('id', ''),
                    source_type=item.get('source_type', ''),
                    source_name=item.get('source_name', ''),
                    source_url=item.get('source_url', ''),
                    created_at=created_at,
                    ingest_status='raw',
                    raw_title=item.get('raw_title', item.get('title', '')),
                    raw_content=item.get('raw_content', item.get('text', '')),
                    raw_html=item.get('raw_html', ''),
                    raw_media=item.get('raw_media', ''),
                    lang=item.get('lang', ''),
                    raw_tags=item.get('raw_tags', ''),
                    checksum=checksum,
                    parse_error=item.get('parse_error', ''),
                    debug_info=item.get('debug_info', '')
                )
                row = [
                    news.id,
                    news.source_type,
                    news.source_name,
                    news.source_url,
                    news.created_at,
                    news.ingest_status,
                    news.raw_title,
                    news.raw_content,
                    news.raw_html,
                    news.raw_media,
                    news.raw_tags,
                    news.checksum,
                    news.parse_error,
                    news.debug_info
                ]
                raw_rows.append(row)
            except Exception as e:
                logger.error(f"Ошибка валидации NewsItem: {e}")
        # Фильтрация по id (уникальность)
        new_rows = [row for row in raw_rows if row[0] not in existing_ids]
        if new_rows:
            worksheets['raw_feed'].append_rows(new_rows)
            logger.info(f"Добавлено {len(new_rows)} записей в Google Sheets (raw_feed)")
        # --- Автоматическая запись новых контактов ---
        # Ожидается, что item['contacts'] - список контактов с contact_id
        contacts_rows = []
        existing_contact_ids = set(worksheets['contacts'].col_values(1))
        for item in data:
            contacts = item.get('contacts', [])
            for contact in contacts:
                contact_id = contact.get('contact_id')
                if contact_id and contact_id not in existing_contact_ids:
                    contacts_rows.append([
                        contact_id,
                        contact.get('type', ''),
                        contact.get('value', ''),
                        contact.get('source', ''),
                        contact.get('date_found', ''),
                    ])
                    existing_contact_ids.add(contact_id)
        if contacts_rows:
            worksheets['contacts'].append_rows(contacts_rows)
            logger.info(f"Добавлено {len(contacts_rows)} новых контактов в Google Sheets (contacts)")
    except Exception as e:
        logger.error(f"Ошибка при сохранении в Google Sheets: {e}")

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
            return None, None

        return file, media_type

    except Exception as e:
        logger.error(f"Ошибка загрузки медиа: {str(e)}")
        return None, None

async def safe_request(func, *args, **kwargs):
    """Улучшенный безопасный запрос"""
    try:
        result = await func(*args, **kwargs)
        if asyncio.isfuture(result) or asyncio.iscoroutine(result):
            return await result
        return result
    except (ClientError, TimeoutError) as e:
        logger.warning(f"Сетевая ошибка: {str(e)}")
        await asyncio.sleep(5)
        return await safe_request(func, *args, **kwargs)
    except FloodWaitError as e:
        logger.warning(f"FloodWait: sleeping {e.seconds} seconds")
        await asyncio.sleep(e.seconds)
        return await safe_request(func, *args, **kwargs)

def parse_youtube(url):
    """Извлекает RSS URL из URL канала YouTube."""
    try:
        if "youtube.com/channel/" in url:
            channel_id = url.split("/channel/")[-1].split('?')[0]
        elif "youtube.com/user/" in url:
            channel_id = url.split("/user/")[-1].split('?')[0]
        else:
            raise ValueError("Неподдерживаемый формат URL YouTube")
            
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    except Exception as e:
        logger.error(f"Ошибка обработки YouTube URL: {str(e)}")
        raise ValueError("Некорректный URL YouTube")

async def main():
    handle_signals()
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

            google_sheets = await init_google_sheets()
            if not google_sheets:
                logger.error("Не удалось инициализировать Google Sheets")
                return

            parsers = {
                "telegram": TelegramParser(client),
                "rss": RSSParser(),
                "website": WebsiteParser(),
                "youtube": YoutubeParser(config.YOUTUBE_API_KEY)
            }

            while True:
                try:
                    all_data = []
                    tasks = []
                    for source in list_sources():
                        if source.type == 'youtube':
                            try:
                                source.url = parse_youtube(source.url)
                                source.type = 'rss'
                            except ValueError as e:
                                logger.error(f"Ошибка YouTube: {e}")
                                continue
                        parser = parsers.get(source.type)
                        if parser:
                            tasks.append(parser.parse(source))

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"Ошибка при парсинге: {result}", exc_info=True)
                        elif result:
                            all_data.extend(result)
                            logger.info(f"Получено {len(result)} записей")

                    if all_data:
                        await save_to_google_sheets(google_sheets, all_data)
                        logger.info(f"Всего сохранено {len(all_data)} записей")

                    await asyncio.sleep(getattr(config, 'PARSING_INTERVAL', 3600))

                except RpcCallFailError as e:
                    logger.error(f"Сетевая ошибка: {e}")
                    await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"Критическая ошибка: {e}", exc_info=True)
                    await asyncio.sleep(60)

    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}", exc_info=True)
    finally:
        if client:
            await client.disconnect()
# Остальной код остается без изменений
# ... [оставшийся код] ...

if __name__ == "__main__":
    logger.info("Запуск основного цикла парсинга...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
