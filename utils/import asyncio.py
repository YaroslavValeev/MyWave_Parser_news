import asyncio
import logging
import signal
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List
import requests
from bs4 import BeautifulSoup
import feedparser
from abc import ABC, abstractmethod
import tenacity # type: ignore

from dotenv import load_dotenv
from telethon import TelegramClient, types
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError, SessionPasswordNeededError
from google.oauth2.service_account import Credentials
import gspread
from googleapiclient.discovery import build # type: ignore
from youtube_transcript_api import YouTubeTranscriptApi

# Imports from local modules (ensure these are available in your project)
from utils.helpers import clean_text, extract_youtube_video_id
from storage.sources import list_sources
from config.settings import config
from collectors.telegram_parser import download_media
from telegram_rate_limiter import safe_send_message, RateLimiter
from utils.telegram_session import TelegramSessionManager

# Load environment variables from .env file
load_dotenv()

# Улучшенная настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", mode='w'),
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
    @staticmethod
    async def human_delay():
        delay = random.uniform(1.5, 4.0)
        logger.debug(f"Задержка: {delay:.2f} сек")
        await asyncio.sleep(delay)

    @staticmethod
    def is_blocked(response):
        return response.status_code == 429 or "captcha" in response.text

# Добавляем абстрактный класс BaseParser
class BaseParser(ABC):
    def __init__(self, limit=50):
        self.limit = limit

    @abstractmethod
    async def parse(self, source):
        """Абстрактный метод для парсинга данных из источника."""
        pass

    def validate_source(self, source):
        """Проверка корректности источника перед парсингом."""
        if not hasattr(source, 'url') or not source.url:
            raise ValueError(f"Некорректный источник: {source}")
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
    """Инициализация Google Sheets с обработкой ошибок"""
    try:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE,  # Исправлено на GOOGLE_CREDENTIALS_FILE
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        doc = client.open_by_key(config.GOOGLE_SHEET_ID)
        
        worksheets = {
            'news': doc.worksheet("news_articles"),
            'contacts': doc.worksheet("contacts")
        }
        
        headers = ["ID", "Date", "Source Type", "Source URL", "Title",
                   "Content", "Images", "Videos", "Status"]
        
        # Check if the news worksheet is empty; if so, add header row.
        if not worksheets['news'].get_all_values():
            worksheets['news'].append_row(headers)
            
        logger.info("Google Sheets initialized")
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

# Создаем класс TelegramParser, наследуясь от BaseParser
class TelegramParser(BaseParser):
    def __init__(self, client, limit=50):
        super().__init__(limit)
        self.client = client

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source):
        """Улучшенный парсер Telegram с выводом результатов"""
        self.validate_source(source)
        try:
            await SafeParser.human_delay()
            await check_session(self.client)
            
            entity = await self.client.get_entity(source.url)
            logger.info(f"Канал найден: {entity.title}")

            messages = []
            async for message in self.client.iter_messages(entity, limit=self.limit):
                try:
                    await SafeParser.human_delay()
                    unique_id = generate_unique_id()
                    text = message.text or ""
                    media_path = None
                    media_type = None
                    if message.media:
                        media_path, media_type = await download_media(message)

                    author_info = await get_author_info(self.client, message)
                    content_type = classify_telegram_content(text)

                    # Safeguard: if message.date is None, fallback to current time
                    if message.date:
                        msg_date = message.date.isoformat()
                    else:
                        msg_date = datetime.now(timezone.utc).isoformat()

                    msg_data = {
                        'id': unique_id,
                        'date': msg_date,
                        'source_type': 'telegram',
                        'source_url': f"https://t.me/{entity.username}" if entity.username else source.url,
                        'content_type': content_type,
                        'title': entity.title,
                        'text': clean_text(text),
                        'images': media_path if media_type == "photo" else '',
                        'videos': media_path if media_type == "video" else '',
                        'status': 'new',
                    }
                    messages.append(msg_data)

                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения: {e}")

            return messages

        except Exception as e:
            logger.error(f"Ошибка парсинга Telegram: {e}")
            return []

# Создаем класс RSSParser, наследуясь от BaseParser
class RSSParser(BaseParser):
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """Парсинг RSS лент"""
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
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """Парсинг веб-сайтов"""
        self.validate_source(source)
        try:
            response = requests.get(source.url, timeout=10)
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
        except Exception as e:
            logger.error(f"Ошибка парсинга сайта: {e}")
            return []

# Создаем класс YoutubeParser, наследуясь от BaseParser
class YoutubeParser(BaseParser):
    def __init__(self, api_key, limit=50):
        super().__init__(limit)
        self.api_key = api_key
        self.youtube = build('youtube', 'v3', developerKey=self.api_key)

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source) -> List[Dict]:
        """Парсинг YouTube каналов (используем API)"""
        self.validate_source(source)
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
    """Сохранение данных в Google Sheets с новой структурой"""
    try:
        # Подготовка данных для таблицы news_articles
        news_rows = []
        existing_ids = worksheets['news'].col_values(1)
        for item in data:
            if item.get('id') not in existing_ids:
                news_rows.append([
                    item.get('id', ''),
                    item.get('date', ''),
                    item.get('source_type', ''),
                    item.get('source_url', ''),
                    item.get('content_type', ''),
                    item.get('title', ''),
                    item.get('text', ''),
                    item.get('url', ''),
                    item.get('author_id', ''),
                    item.get('author_name', '')
                ])
        
        if news_rows:
            worksheets['news'].append_rows(news_rows)
            logger.info(f"Added {len(news_rows)} records to Google Sheets (news_articles)")
            
        # Обновление contacts (если есть информация об авторах)
        contacts_data = {}
        for item in data:
            if 'author_id' in item and item['author_id']:
                key = (item['author_id'], item.get('author_name', ''))
                contacts_data.setdefault(key, 0)
                contacts_data[key] += 1
                
        if contacts_data:
            contacts_rows = [[uid, name] for (uid, name) in contacts_data.keys()]
            if contacts_rows:
                worksheets['contacts'].append_rows(contacts_rows)
                logger.info(f"Added {len(contacts_rows)} records to Google Sheets (contacts)")

    except Exception as e:
        logger.error(f"Error saving to Google Sheets: {e}")

def handle_signals():
    """Обработчик сигналов"""
    def shutdown(signum, frame):
        logger.info("Завершение работы...")
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

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
    
    try:
        google_sheets = await init_google_sheets()
        if not google_sheets:
            logger.error("Не удалось инициализировать Google Sheets")
            return
            
        rate_limiter = RateLimiter(requests_per_minute=getattr(config, 'TELEGRAM_REQUESTS_PER_MINUTE', 30))
        session_manager = TelegramSessionManager(config.TELEGRAM_API_ID_USER, config.TELEGRAM_API_HASH_USER, config.TELEGRAM_PHONE)
        
        parsers = {
            "telegram": TelegramParser(await session_manager.get_client()),
            "rss": RSSParser(),
            "website": WebsiteParser(),
            "youtube": YoutubeParser(config.YOUTUBE_API_KEY)
        }

        while True:
            all_data = []
            tasks = []
            for source in list_sources():
                parser = parsers.get(source.type)
                if parser:
                    tasks.append(parser.parse(source))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Ошибка при парсинге: {result}")
                elif result:
                    all_data.extend(result)
                    logger.info(f"Получено {len(result)} записей")
            
            if all_data:
                await save_to_google_sheets(google_sheets, all_data)
                logger.info(f"Всего сохранено {len(all_data)} записей")
                
            await asyncio.sleep(getattr(config, 'PARSING_INTERVAL', 3600))
                
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user")
    except Exception as err:
        logger.error(f"Unhandled error: {err}")
    finally:
        await session_manager.close_client()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as err:
        logger.error(f"Unhandled error: {err}")