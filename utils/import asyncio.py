import asyncio
import logging
import signal
import os
import random
import time
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, List
import requests
from bs4 import BeautifulSoup
import feedparser
from abc import ABC, abstractmethod
import tenacity # type: ignore
import hashlib

from dotenv import load_dotenv
from telethon import TelegramClient, types
from telethon.types import MessageMediaPhoto, MessageMediaDocument
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError, SessionPasswordNeededError
from google.oauth2.service_account import Credentials
import gspread
from googleapiclient.discovery import build # type: ignore
from youtube_transcript_api import YouTubeTranscriptApi

# Imports from local modules (ensure these are available in your project)
from utils.helpers import clean_text, extract_youtube_video_id
from storage.sources import list_sources
from config.settings import config
from telegram_rate_limiter import safe_send_message, RateLimiter
# TelegramSessionManager импортируется напрямую из корня проекта
import sys
import os
_root_dir = os.path.dirname(os.path.dirname(__file__))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)
from telegram_session import TelegramSessionManager
from utils.row_utils import generate_checksum, validate_raw_row
from utils.sheet_schema import RAW_FEED_COLUMNS, DEFAULTS

# Локальная функция для скачивания медиа из Telegram
async def download_media(client, message):
    """Скачивание медиа с повторными попытками"""
    if not hasattr(message, 'media') or not message.media:
        return None, None
    try:
        # Создаем папку media если её нет (используем абсолютный путь для Windows)
        media_dir = os.path.join(os.getcwd(), "media")
        os.makedirs(media_dir, exist_ok=True)
        
        # Определяем тип медиа
        if isinstance(message.media, MessageMediaPhoto):
            media_type = "photo"
            file = await client.download_media(
                message.media,
                file=os.path.join(media_dir, f"{uuid.uuid4()}.jpg")
            )
        elif isinstance(message.media, MessageMediaDocument):
            media_type = "video"
            doc = message.media.document
            ext = doc.mime_type.split('/')[-1] if doc.mime_type else 'unknown'
            file = await client.download_media(
                doc,
                file=os.path.join(media_dir, f"{uuid.uuid4()}.{ext}")
            )
        else:
            # Неизвестный тип медиа
            return None, None
        return file, media_type
    except Exception as e:
        logger.error(f"Ошибка при скачивании медиа: {e}")
        return None, None

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
    async def parse(self, source, date_from=None, date_to=None):
        """
        Абстрактный метод для парсинга данных из источника.
        
        Args:
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
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

# Кэш для документа Google Sheets (чтобы не переоткрывать каждый раз)
_gs_doc_cache = None

def clear_gs_cache():
    """Очищает кэш документа Google Sheets (например, при ошибках подключения)"""
    global _gs_doc_cache
    _gs_doc_cache = None
    logger.debug("Кэш документа Google Sheets очищен")

async def init_google_sheets():
    """Инициализация Google Sheets с обработкой ошибок"""
    global _gs_doc_cache
    
    # Если документ уже закэширован, возвращаем его
    if _gs_doc_cache is not None:
        logger.debug("Используется закэшированный документ Google Sheets")
        return _gs_doc_cache
    
    creds_data = None  # Сохраняем для использования в диагностике ошибок
    try:
        # Логируем путь к файлу credentials
        credentials_path = config.GOOGLE_CREDENTIALS_FILE
        logger.info(f"Попытка инициализации Google Sheets с файлом: {credentials_path}")
        
        # Проверяем существование файла
        if not os.path.exists(credentials_path):
            logger.error(f"Файл credentials не найден по пути: {credentials_path}")
            return None
        
        # Проверяем, что это файл (а не директория)
        if not os.path.isfile(credentials_path):
            logger.error(f"Указанный путь не является файлом: {credentials_path}")
            return None
        
        # Логируем размер файла
        file_size = os.path.getsize(credentials_path)
        logger.info(f"Размер файла credentials: {file_size} байт")
        
        # Пытаемся прочитать файл для проверки формата и структуры
        try:
            with open(credentials_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                first_chars = file_content[:100]
                if first_chars.strip().startswith('{'):
                    logger.info("Файл credentials выглядит как валидный JSON (начинается с '{')")
                    # Пытаемся парсить JSON для проверки структуры
                    try:
                        creds_data = json.loads(file_content)
                        # Логируем структуру без секретных данных
                        required_fields = ['type', 'project_id', 'private_key_id', 'client_email']
                        missing_fields = [field for field in required_fields if field not in creds_data]
                        if missing_fields:
                            logger.warning(f"В файле credentials отсутствуют обязательные поля: {missing_fields}")
                        else:
                            logger.info(f"Файл credentials содержит все обязательные поля. Тип: {creds_data.get('type')}, "
                                       f"Project ID: {creds_data.get('project_id')}, "
                                       f"Client Email: {creds_data.get('client_email')}")
                            # Проверяем наличие private_key
                            if 'private_key' not in creds_data or not creds_data['private_key']:
                                logger.error("В файле credentials отсутствует или пустой private_key!")
                            else:
                                # Проверяем формат ключа (должен начинаться с -----BEGIN)
                                if '-----BEGIN' not in creds_data['private_key']:
                                    logger.error("private_key в файле credentials имеет неверный формат (должен быть PEM)")
                                else:
                                    logger.info("private_key найден и имеет правильный формат (PEM)")
                                    # Проверяем длину ключа (должен быть достаточно длинным)
                                    key_length = len(creds_data['private_key'])
                                    if key_length < 100:
                                        logger.warning(f"private_key кажется слишком коротким ({key_length} символов)")
                                    else:
                                        logger.debug(f"private_key имеет длину {key_length} символов (нормально)")
                    except json.JSONDecodeError as json_err:
                        logger.error(f"Файл credentials не является валидным JSON: {json_err}")
                        return None
                else:
                    logger.warning(f"Файл credentials не начинается с '{{' (первые символы: {first_chars[:50]})")
        except Exception as read_err:
            logger.error(f"Не удалось прочитать файл credentials: {read_err}", exc_info=True)
            return None
        
        # Пытаемся создать credentials
        logger.info("Создание Credentials из файла...")
        try:
            creds = Credentials.from_service_account_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            logger.info("Credentials успешно созданы из файла")
        except Exception as creds_err:
            logger.error(f"Ошибка при создании Credentials: {creds_err}", exc_info=True)
            return None
        
        # Проверяем, что credentials валидны (не истекли)
        try:
            if hasattr(creds, 'expired') and creds.expired:
                logger.warning("Credentials истекли, пытаемся обновить...")
                # Пытаемся обновить токен
                if hasattr(creds, 'refresh'):
                    from google.auth.transport.requests import Request
                    request = Request()
                    creds.refresh(request)
                    logger.debug("Credentials успешно обновлены")
        except Exception as refresh_err:
            logger.warning(f"Не удалось обновить credentials (возможно, это нормально): {refresh_err}")
        
        # Авторизуемся в gspread
        logger.info("Авторизация в gspread...")
        try:
            client = gspread.authorize(creds)
            logger.info("Успешная авторизация в gspread")
        except Exception as auth_err:
            logger.error(f"Ошибка авторизации в gspread: {auth_err}", exc_info=True)
            return None
        
        # Проверяем системное время (JWT подпись зависит от времени)
        import time as time_module
        current_time = time_module.time()
        logger.info(f"Текущее системное время (Unix timestamp): {current_time}")
        from datetime import datetime
        logger.info(f"Текущее системное время (человекочитаемое): {datetime.fromtimestamp(current_time)}")
        
        # Проверяем, что время не слишком отличается от реального (допустимый разброс ±5 минут)
        # Для этого можно попробовать получить время от Google (но это необязательно)
        
        # Открываем документ
        sheet_id = config.GOOGLE_SHEET_ID
        logger.info(f"Открытие документа Google Sheets с ID: {sheet_id}")
        try:
            # Пытаемся сначала проверить доступ через список документов
            try:
                # Попробуем получить список доступных таблиц (если есть доступ)
                logger.info("Попытка получить список доступных таблиц для проверки доступа...")
                # Это может не сработать, но попробуем
            except Exception as list_err:
                logger.debug(f"Не удалось получить список таблиц (это нормально): {list_err}")
            
            # Пытаемся открыть документ напрямую
            logger.info("Попытка открыть документ напрямую...")
            doc = client.open_by_key(sheet_id)
            logger.info(f"Google Sheets initialized successfully. Документ: {doc.title if hasattr(doc, 'title') else 'unknown'}")
            
            # Автоприведение заголовков листа raw_feed к схеме
            logger.info("Проверка и обновление заголовков листа raw_feed...")
            await ensure_sheet_headers(doc, 'raw_feed')
            
            # Сохраняем документ в кэш
            _gs_doc_cache = doc
            logger.debug("Документ Google Sheets сохранен в кэш")
            
            return doc
        except Exception as doc_err:
            error_msg = str(doc_err)
            logger.error(f"Ошибка при открытии документа Google Sheets (ID: {sheet_id}): {doc_err}", exc_info=True)
            
            # Дополнительная диагностика для ошибки JWT Signature
            if 'Invalid JWT Signature' in error_msg or 'invalid_grant' in error_msg:
                logger.error("=" * 80)
                logger.error("ДИАГНОСТИКА ОШИБКИ 'Invalid JWT Signature':")
                logger.error("=" * 80)
                logger.error("Возможные причины:")
                logger.error("1. Сервисный аккаунт был удален или отключен в Google Cloud Console")
                logger.error("2. Ключ сервисного аккаунта был отозван или пересоздан")
                logger.error("3. Системное время компьютера сильно отличается от реального времени")
                logger.error("4. Сервисный аккаунт не имеет доступа к таблице")
                logger.error("5. Файл credentials.json был изменен или поврежден")
                logger.error("")
                logger.error("РЕШЕНИЕ:")
                logger.error("1. Проверьте системное время на компьютере")
                logger.error("2. Убедитесь, что сервисный аккаунт активен в Google Cloud Console")
                logger.error("3. Убедитесь, что сервисный аккаунт имеет доступ к таблице:")
                logger.error(f"   - Откройте таблицу: https://docs.google.com/spreadsheets/d/{sheet_id}")
                logger.error(f"   - Нажмите 'Настройки доступа' (Share)")
                if creds_data and 'client_email' in creds_data:
                    logger.error(f"   - Добавьте email сервисного аккаунта: {creds_data.get('client_email')}")
                else:
                    logger.error("   - Добавьте email сервисного аккаунта из файла credentials.json")
                logger.error("4. Скачайте новый файл credentials.json из Google Cloud Console")
                logger.error("=" * 80)
            
            # Очищаем кэш при ошибке, чтобы при следующей попытке было новое подключение
            clear_gs_cache()
            return None
            
    except FileNotFoundError as e:
        logger.error(f"Файл не найден: {e}")
        clear_gs_cache()
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON в файле credentials: {e}")
        clear_gs_cache()
        return None
    except Exception as e:
        logger.error(f"Ошибка Google Sheets (общая): {e}", exc_info=True)
        clear_gs_cache()
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
    async def parse(self, source, date_from=None, date_to=None):
        """
        Улучшенный парсер Telegram с выводом результатов
        
        Args:
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
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
                    
                    # Фильтрация по дате
                    if date_from or date_to:
                        if message.date:
                            msg_dt = message.date
                            if date_from and msg_dt < date_from:
                                continue
                            if date_to and msg_dt > date_to:
                                continue
                        elif date_from:
                            # Если нет даты и требуется фильтрация от даты, пропускаем
                            continue
                    
                    unique_id = generate_unique_id()
                    text = message.text or ""
                    media_path = None
                    media_type = None
                    if message.media:
                        media_path, media_type = await download_media(self.client, message)

                    author_info = await get_author_info(self.client, message)
                    content_type = classify_telegram_content(text)

                    # Safeguard: if message.date is None, fallback to current time
                    if message.date:
                        msg_date = message.date.isoformat()
                        original_published_at = msg_date
                    else:
                        msg_date = datetime.now(timezone.utc).isoformat()
                        original_published_at = ''

                    # Формируем media_json если есть медиа
                    media_json = ''
                    if media_path:
                        import json
                        media_data = {
                            'type': media_type,
                            'path': media_path
                        }
                        media_json = json.dumps(media_data, ensure_ascii=False)

                    # Определяем source_item_id (ID сообщения из Telegram)
                    source_item_id = str(message.id) if hasattr(message, 'id') else ''
                    
                    # Формируем source_url и canonical_url (для Telegram они совпадают)
                    source_url = f"https://t.me/{entity.username}/{message.id}" if hasattr(entity, 'username') and entity.username else source.url
                    canonical_url = source_url
                    
                    # Вытаскиваем хэштеги из текста
                    import re
                    hashtags = re.findall(r'#\w+', text)
                    raw_tags = ', '.join(hashtags) if hashtags else ''
                    
                    # Извлекаем cover_image_url из медиа (приоритет: вложение Telegram)
                    cover_image_url = ''
                    try:
                        from utils.media_utils import extract_cover_image_url
                        cover_image_url = extract_cover_image_url({
                            'media_json': media_json,
                            'raw_media': media_path if media_path else '',
                            'source_url': source_url
                        }, prefer_largest=True) or ''
                    except Exception as e:
                        logger.debug(f"Не удалось извлечь cover_image_url из Telegram: {e}")
                    
                    # Формируем базовый словарь для генерации checksum
                    msg_data_base = {
                        'raw_title': clean_text(text)[:200] if text else '',
                        'raw_content': clean_text(text),
                        'source_url': source_url,
                    }
                    
                    # Генерируем checksum ДО записи (обязательно!)
                    checksum = generate_checksum(msg_data_base)

                    msg_data = {
                        'id': unique_id,
                        'source_type': 'telegram',
                        'source_name': entity.title if hasattr(entity, 'title') else source.name,
                        'source_url': source_url,
                        'source_item_id': source_item_id,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'original_published_at': original_published_at,
                        'raw_title': msg_data_base['raw_title'],
                        'raw_content': msg_data_base['raw_content'],
                        'raw_html': '',  # В Telegram нет HTML
                        'raw_media': media_path if media_path else '',
                        'media_json': media_json,
                        'content_format': 'text',  # Telegram сообщения - текст
                        'lang': 'ru',  # По умолчанию русский
                        'raw_tags': raw_tags,
                        'status': 'DRAFT',  # По умолчанию DRAFT на этапе ingest
                        'ingest_status': 'ok',  # ok если успешно собрано (согласно контракту)
                        'ingest_attempts': 1,
                        'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                        'parse_error': '',
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                        'checksum': checksum,  # Генерируется ДО записи
                        'canonical_url': canonical_url,
                        'cover_image_url': cover_image_url,  # Извлекается из медиа Telegram
                        'debug_info': f"media_type: {media_type}" if media_type else '',
                    }
                    messages.append(msg_data)

                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения: {e}")

            return messages

        except Exception as e:
            logger.error(f"Ошибка парсинга Telegram: {e}")
            # При ошибке парсинга возвращаем запись с ошибкой
            try:
                error_item = {
                    'id': generate_unique_id(),
                    'source_type': 'telegram',
                    'source_name': getattr(source, 'name', ''),
                    'source_url': getattr(source, 'url', ''),
                    'source_item_id': '',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': '',
                    'raw_title': '',
                    'raw_content': '',
                    'raw_html': '',
                    'raw_media': '',
                    'media_json': '',
                    'content_format': 'text',
                    'lang': 'ru',
                    'raw_tags': '',
                    'status': 'DRAFT',
                    'ingest_status': 'error',
                    'ingest_error': str(e),
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': str(e),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': '',
                    'debug_info': f"error_type: {type(e).__name__}"
                }
                return [error_item]
            except:
                return []

# Создаем класс RSSParser, наследуясь от BaseParser
class RSSParser(BaseParser):
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source, date_from=None, date_to=None) -> List[Dict]:
        """
        Парсинг RSS лент
        
        Args:
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
        self.validate_source(source)
        try:
            feed = feedparser.parse(source.url)
            items = []
            
            for entry in feed.entries[:self.limit]:  # Ограничиваем количество записей
                entry_date = get_rss_date(entry)
                
                # Фильтрация по дате
                if date_from or date_to:
                    try:
                        if isinstance(entry_date, str):
                            # Парсим строку даты в datetime
                            try:
                                entry_dt = datetime.fromisoformat(entry_date.replace('Z', '+00:00'))
                            except:
                                # Если не удалось распарсить, пропускаем фильтрацию
                                entry_dt = None
                        else:
                            entry_dt = entry_date
                        
                        if entry_dt:
                            if date_from and entry_dt < date_from:
                                continue
                            if date_to and entry_dt > date_to:
                                continue
                    except Exception as e:
                        logger.debug(f"Ошибка фильтрации по дате для RSS entry: {e}")
                
                entry_link = entry.get('link', source.url)
                
                # Определяем content_format (RSS обычно содержит HTML)
                content_format = 'markdown' if entry.get('description', '').strip().startswith('<') else 'text'
                
                # Формируем media_json если есть медиа в RSS
                media_json = ''
                cover_image_url = ''
                if hasattr(entry, 'media_content') or hasattr(entry, 'enclosures'):
                    import json
                    media_list = []
                    if hasattr(entry, 'enclosures'):
                        for enc in entry.enclosures:
                            enc_type = enc.get('type', '')
                            enc_url = enc.get('href', '')
                            if enc_type.startswith('image'):
                                media_list.append({'type': 'image', 'url': enc_url})
                            elif enc_type.startswith('video'):
                                media_list.append({'type': 'video', 'url': enc_url})
                    if media_list:
                        media_json = json.dumps(media_list, ensure_ascii=False)
                        # Извлекаем cover_image_url из media_json (приоритет: rss enclosure)
                        try:
                            from utils.media_utils import extract_cover_image_url
                            cover_image_url = extract_cover_image_url({'media_json': media_json, 'raw_html': entry.get('description', ''), 'source_url': entry_link}, prefer_largest=True) or ''
                        except Exception as e:
                            logger.debug(f"Не удалось извлечь cover_image_url из RSS: {e}")
                
                # Подготавливаем данные для генерации checksum
                raw_title = clean_text(entry.get('title', ''))
                raw_content = clean_text(entry.get('description', ''))
                raw_html = entry.get('description', '')  # RSS может содержать HTML
                
                # Генерируем checksum ДО записи (на основе содержимого: raw_title + raw_content + raw_html)
                item_base = {
                    'raw_title': raw_title,
                    'raw_content': raw_content,
                    'raw_html': raw_html,
                }
                checksum = generate_checksum(item_base)
                
                items.append({
                    'id': generate_unique_id(),
                    'source_type': 'rss',
                    'source_name': source.name,
                    'source_url': entry_link,
                    'source_item_id': entry.get('id', entry_link),  # ID из RSS или ссылка
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': entry_date,
                    'raw_title': raw_title,
                    'raw_content': raw_content,
                    'raw_html': entry.get('description', ''),  # RSS может содержать HTML
                    'raw_media': '',
                    'media_json': media_json,
                    'content_format': content_format,
                    'lang': 'ru',  # Можно определить по содержимому
                    'raw_tags': ', '.join([tag.get('term', '') for tag in entry.get('tags', [])]),
                    'status': 'DRAFT',
                    'ingest_status': 'ok',
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': '',
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': checksum,  # Генерируется ДО записи
                    'canonical_url': entry_link,  # Для RSS canonical_url = source_url
                    'cover_image_url': cover_image_url,  # Извлекается из RSS enclosure или og:image
                    'debug_info': f"feed_title: {feed.feed.get('title', '')}"
                })
                
            return items
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS: {e}")
            # При ошибке парсинга возвращаем запись с ошибкой
            try:
                error_item = {
                    'id': generate_unique_id(),
                    'source_type': 'rss',
                    'source_name': getattr(source, 'name', ''),
                    'source_url': getattr(source, 'url', ''),
                    'source_item_id': '',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': '',
                    'raw_title': '',
                    'raw_content': '',
                    'raw_html': '',
                    'raw_media': '',
                    'media_json': '',
                    'content_format': 'text',
                    'lang': 'ru',
                    'raw_tags': '',
                    'status': 'DRAFT',
                    'ingest_status': 'error',
                    'ingest_error': str(e),
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': str(e),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': '',
                    'debug_info': f"error_type: {type(e).__name__}"
                }
                return [error_item]
            except:
                return []

# Создаем класс WebsiteParser, наследуясь от BaseParser
class WebsiteParser(BaseParser):
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(3),
        reraise=True
    )
    async def parse(self, source, date_from=None, date_to=None) -> List[Dict]:
        """
        Парсинг веб-сайтов
        
        Args:
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
        self.validate_source(source)
        try:
            response = requests.get(
                source.url,
                timeout=config.WEBSITE_REQUEST_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (MyWaveParser/1.0)"}
            )
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Пример для wakeboardingmag.com - нужно адаптировать под каждый сайт
            articles = []
            for article in soup.select('article')[:self.limit]:  # Limit to 20 entries.
                title_elem = article.find('h2')
                title = title_elem.get_text(strip=True) if title_elem else ''
                content_elem = article.find('div', class_='entry-content')
                content = content_elem.get_text(strip=True) if content_elem else ''
                
                if title or content:
                    # Извлекаем ссылку на статью если есть
                    article_link = source.url
                    link_elem = article.find('a', href=True)
                    if link_elem:
                        import urllib.parse
                        article_link = urllib.parse.urljoin(source.url, link_elem['href'])
                    
                    # Извлекаем дату публикации если есть
                    original_published_at = ''
                    date_elem = article.find(['time', 'span'], class_=['date', 'published', 'time'])
                    if date_elem:
                        original_published_at = date_elem.get('datetime', '') or date_elem.get_text(strip=True)
                    
                    # Извлекаем медиа из статьи
                    media_json = ''
                    images = article.find_all('img')
                    if images:
                        import json
                        media_list = []
                        for img in images[:5]:  # Максимум 5 изображений
                            img_url = img.get('src', '') or img.get('data-src', '')
                            if img_url:
                                import urllib.parse
                                img_url = urllib.parse.urljoin(source.url, img_url)
                                media_list.append({'type': 'image', 'url': img_url})
                        if media_list:
                            media_json = json.dumps(media_list, ensure_ascii=False)
                    
                    # Подготавливаем данные для генерации checksum
                    raw_title = clean_text(title)
                    raw_content = clean_text(content)
                    raw_html = str(article) if article else ''  # Сохраняем HTML для дедупликации
                    
                    # Генерируем checksum ДО записи (на основе содержимого: raw_title + raw_content + raw_html)
                    article_base = {
                        'raw_title': raw_title,
                        'raw_content': raw_content,
                        'raw_html': raw_html,
                    }
                    checksum = generate_checksum(article_base)
                    
                    articles.append({
                        'id': generate_unique_id(),
                        'source_type': 'website',
                        'source_name': source.name,
                        'source_url': article_link,
                        'source_item_id': article_link,  # Используем ссылку как ID
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'original_published_at': original_published_at,
                        'raw_title': raw_title,
                        'raw_content': raw_content,
                        'raw_html': str(article),  # Сохраняем HTML для дальнейшей обработки
                        'raw_media': '',
                        'media_json': media_json,
                        'content_format': 'markdown',  # HTML контент, будет конвертирован в markdown
                        'lang': 'ru',
                        'raw_tags': '',
                        'status': 'DRAFT',
                        'ingest_status': 'ok',
                        'ingest_attempts': 1,
                        'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                        'parse_error': '',
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                        'checksum': checksum,  # Генерируется ДО записи
                        'canonical_url': article_link,  # Для сайтов canonical_url = source_url
                        'cover_image_url': '',  # Пока оставляем пустым
                        'debug_info': f"article_class: {article.get('class', [])}"
                    })
                    
            return articles
        except Exception as e:
            logger.error(f"Ошибка парсинга сайта: {e}")
            # При ошибке парсинга возвращаем запись с ошибкой
            try:
                error_item = {
                    'id': generate_unique_id(),
                    'source_type': 'website',
                    'source_name': getattr(source, 'name', ''),
                    'source_url': getattr(source, 'url', ''),
                    'source_item_id': '',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': '',
                    'raw_title': '',
                    'raw_content': '',
                    'raw_html': '',
                    'raw_media': '',
                    'media_json': '',
                    'content_format': 'text',
                    'lang': 'ru',
                    'raw_tags': '',
                    'status': 'DRAFT',
                    'ingest_status': 'error',
                    'ingest_error': str(e),
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': str(e),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': '',
                    'debug_info': f"error_type: {type(e).__name__}"
                }
                return [error_item]
            except:
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
    async def parse(self, source, date_from=None, date_to=None) -> List[Dict]:
        """
        Парсинг YouTube каналов или конкретных видео (используем API)
        
        Args:
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
        self.validate_source(source)
        url = getattr(source, 'url', '')
        
        # Проверяем, является ли URL прямой ссылкой на видео
        from utils.helpers import extract_youtube_video_id
        video_id = extract_youtube_video_id(url)
        
        if video_id:
            # Это прямая ссылка на видео - парсим конкретное видео
            return await self._parse_single_video(video_id, source, date_from, date_to)
        
        # Иначе это канал - парсим канал
        # --- Новый блок: определяем channel_id ---
        channel_id = getattr(source, 'channel_id', None)
        if not channel_id:
            # Пробуем извлечь из url
            import re
            match = re.search(r'(?:/channel/|/user/|/c/)?([A-Za-z0-9_-]{16,})', url)
            if match:
                channel_id = match.group(1)
            else:
                logger.error(f"Не удалось определить channel_id для источника YouTube: {source}")
                return []
        
        # Парсим канал
        try:
            request = self.youtube.search().list(
                part="snippet",
                channelId=channel_id,
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

                # Формируем media_json для YouTube видео
                import json
                thumbnail_url = item['snippet'].get('thumbnails', {}).get('high', {}).get('url', '') or item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', '')
                media_json = json.dumps({
                    'type': 'video',
                    'url': video_url,
                    'video_id': video_id,
                    'thumbnail': thumbnail_url
                }, ensure_ascii=False)
                
                # Извлекаем cover_image_url из thumbnail YouTube (приоритет: thumbnail видео)
                cover_image_url = ''
                try:
                    from utils.media_utils import extract_cover_image_url
                    cover_image_url = extract_cover_image_url({
                        'media_json': json.dumps([{'type': 'image', 'url': thumbnail_url}]),
                        'source_url': video_url
                    }, prefer_largest=True) or ''
                except Exception as e:
                    logger.debug(f"Не удалось извлечь cover_image_url из YouTube thumbnail: {e}")
                
                published_at = item['snippet']['publishedAt']
                
                # Фильтрация по дате
                if date_from or date_to:
                    try:
                        # Парсим publishedAt в datetime
                        pub_dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                        if date_from and pub_dt < date_from:
                            continue
                        if date_to and pub_dt > date_to:
                            continue
                    except Exception as e:
                        logger.debug(f"Ошибка фильтрации по дате для YouTube video: {e}")
                
                # Подготавливаем данные для генерации checksum
                raw_title = item['snippet']['title']
                raw_content = item['snippet']['description'] + " " + transcript_text
                
                # Генерируем checksum ДО записи
                video_base = {
                    'raw_title': raw_title,
                    'raw_content': raw_content,
                    'source_url': video_url,
                }
                checksum = generate_checksum(video_base)
                
                items.append({
                    'id': generate_unique_id(),
                    'source_type': 'youtube',
                    'source_name': source.name,
                    'source_url': video_url,
                    'source_item_id': video_id,  # ID видео из YouTube
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': published_at,
                    'raw_title': raw_title,
                    'raw_content': raw_content,
                    'raw_html': '',  # YouTube не предоставляет HTML
                    'raw_media': video_url,
                    'media_json': media_json,
                    'content_format': 'text',  # Описание и транскрипт - текст
                    'lang': item['snippet'].get('defaultLanguage', 'ru'),
                    'raw_tags': ', '.join(item['snippet'].get('tags', [])),
                    'status': 'DRAFT',
                    'ingest_status': 'ok',
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': '',
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': checksum,  # Генерируется ДО записи
                    'canonical_url': video_url,  # Для YouTube canonical_url = source_url
                    'cover_image_url': cover_image_url,  # Извлекается из thumbnail YouTube
                    'debug_info': f"channel_title: {item['snippet'].get('channelTitle', '')}"
                })
            return items
        except Exception as e:
            logger.error(f"YouTube parsing error: {e}")
            # При ошибке парсинга возвращаем запись с ошибкой
            try:
                error_item = {
                    'id': generate_unique_id(),
                    'source_type': 'youtube',
                    'source_name': getattr(source, 'name', ''),
                    'source_url': getattr(source, 'url', ''),
                    'source_item_id': '',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': '',
                    'raw_title': '',
                    'raw_content': '',
                    'raw_html': '',
                    'raw_media': '',
                    'media_json': '',
                    'content_format': 'text',
                    'lang': 'ru',
                    'raw_tags': '',
                    'status': 'DRAFT',
                    'ingest_status': 'error',
                    'ingest_error': str(e),
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': str(e),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': '',
                    'debug_info': f"error_type: {type(e).__name__}"
                }
                return [error_item]
            except:
                return []
    
    async def _parse_single_video(self, video_id: str, source, date_from=None, date_to=None) -> List[Dict]:
        """
        Парсит конкретное YouTube видео по его ID
        
        Args:
            video_id: ID видео из YouTube
            source: Объект источника (NewsSource)
            date_from: Начальная дата для фильтрации (datetime, опционально)
            date_to: Конечная дата для фильтрации (datetime, опционально)
        """
        try:
            # Получаем информацию о видео через YouTube API
            request = self.youtube.videos().list(
                part="snippet,contentDetails",
                id=video_id
            )
            response = request.execute()
            
            if not response.get('items'):
                logger.warning(f"Видео {video_id} не найдено")
                return []
            
            item = response['items'][0]
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Получаем транскрипт
            transcript_text = ""
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id)
                transcript_text = ' '.join([t['text'] for t in transcript])
            except Exception as e:
                logger.warning(f"Не удалось получить транскрипт для видео {video_id}: {e}")
            
            # Формируем media_json для YouTube видео
            import json
            thumbnail_url = item['snippet'].get('thumbnails', {}).get('high', {}).get('url', '') or item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', '')
            media_json = json.dumps([{
                'type': 'video',
                'url': video_url,
                'video_id': video_id,
                'thumbnail': thumbnail_url
            }], ensure_ascii=False)
            
            # Извлекаем cover_image_url из thumbnail YouTube
            cover_image_url = ''
            try:
                from utils.media_utils import extract_cover_image_url
                cover_image_url = extract_cover_image_url({
                    'media_json': json.dumps([{'type': 'image', 'url': thumbnail_url}]),
                    'source_url': video_url
                }, prefer_largest=True) or ''
            except Exception as e:
                logger.debug(f"Не удалось извлечь cover_image_url из YouTube thumbnail: {e}")
            
            published_at = item['snippet']['publishedAt']
            
            # Фильтрация по дате
            if date_from or date_to:
                try:
                    pub_dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    if date_from and pub_dt < date_from:
                        return []
                    if date_to and pub_dt > date_to:
                        return []
                except Exception as e:
                    logger.debug(f"Ошибка фильтрации по дате для YouTube video: {e}")
            
            # Подготавливаем данные для генерации checksum
            raw_title = item['snippet']['title']
            raw_content = item['snippet']['description'] + " " + transcript_text
            raw_html = ''  # YouTube не предоставляет HTML
            
            # Генерируем checksum ДО записи (на основе содержимого: raw_title + raw_content + raw_html)
            video_base = {
                'raw_title': raw_title,
                'raw_content': raw_content,
                'raw_html': raw_html,
            }
            checksum = generate_checksum(video_base)
            
            return [{
                'id': generate_unique_id(),
                'source_type': 'youtube',
                'source_name': source.name,
                'source_url': video_url,
                'source_item_id': video_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'original_published_at': published_at,
                'raw_title': raw_title,
                'raw_content': raw_content,
                'raw_html': '',
                'raw_media': video_url,
                'media_json': media_json,
                'content_format': 'text',
                'lang': item['snippet'].get('defaultLanguage', 'ru'),
                'raw_tags': ', '.join(item['snippet'].get('tags', [])),
                'status': 'DRAFT',
                'ingest_status': 'ok',
                'ingest_attempts': 1,
                'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                'parse_error': '',
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'checksum': checksum,
                'canonical_url': video_url,
                'cover_image_url': cover_image_url,
                'debug_info': f"channel_title: {item['snippet'].get('channelTitle', '')}"
            }]
        except Exception as e:
            logger.error(f"YouTube single video parsing error: {e}", exc_info=True)
            # При ошибке парсинга возвращаем запись с ошибкой
            try:
                error_item = {
                    'id': generate_unique_id(),
                    'source_type': 'youtube',
                    'source_name': getattr(source, 'name', ''),
                    'source_url': getattr(source, 'url', ''),
                    'source_item_id': video_id,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_published_at': '',
                    'raw_title': '',
                    'raw_content': '',
                    'raw_html': '',
                    'raw_media': '',
                    'media_json': '',
                    'content_format': 'text',
                    'lang': 'ru',
                    'raw_tags': '',
                    'status': 'DRAFT',
                    'ingest_status': 'error',
                    'ingest_error': str(e),
                    'ingest_attempts': 1,
                    'ingest_last_try_at': datetime.now(timezone.utc).isoformat(),
                    'parse_error': str(e),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'checksum': '',
                    'debug_info': f"error_type: {type(e).__name__}, video_id: {video_id}"
                }
                return [error_item]
            except:
                return []

async def update_sheet_row(doc, sheet_name, item_data, lookup_field='checksum'):
    """
    Обновляет существующую строку в Google Sheets по checksum или id.
    Использует header-based запись: читает реальные заголовки листа и обновляет только нужные колонки.
    :param doc: gspread документ
    :param sheet_name: имя листа
    :param item_data: словарь с данными для обновления
    :param lookup_field: поле для поиска строки ('checksum' или 'id')
    """
    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return False
    
    try:
        all_values = ws.get_all_values()
        if not all_values or len(all_values) < 2:
            # Это нормальная ситуация при первом запуске или когда данные еще не добавлены
            logger.debug(f"Лист {sheet_name} пуст или содержит только заголовки - обновление невозможно")
            return False
        
        # Читаем реальные заголовки из листа (header-based)
        header = all_values[0]
        
        # Строим маппинг header -> индекс для быстрого поиска
        header_to_idx = {col_name.strip(): idx for idx, col_name in enumerate(header) if col_name and col_name.strip()}
        
        # Находим индекс поля для поиска
        lookup_idx = header_to_idx.get(lookup_field)
        if lookup_idx is None:
            logger.error(f"Поле {lookup_field} не найдено в заголовках листа {sheet_name}")
            return False
        
        # Находим значение для поиска
        lookup_value = item_data.get(lookup_field)
        if not lookup_value:
            logger.warning(f"Значение {lookup_field} отсутствует в item_data")
            return False
        
        # Ищем строку для обновления
        row_num = None
        for idx, row in enumerate(all_values[1:], start=2):  # Начинаем с 2 (первая строка - заголовок)
            if len(row) > lookup_idx and str(row[lookup_idx]).strip() == str(lookup_value).strip():
                row_num = idx
                break
        
        if not row_num:
            logger.debug(f"Строка с {lookup_field}={lookup_value} не найдена в листе {sheet_name}")
            return False
        
        # Получаем текущую строку
        current_row = all_values[row_num - 1] if len(all_values) >= row_num else []
        
        # HEADER-BASED: Обновляем только указанные поля по их реальным индексам в листе
        # Сначала копируем текущую строку
        updated_row = current_row.copy() if current_row else []
        
        # Расширяем строку до нужной длины (если заголовков больше, чем значений в строке)
        while len(updated_row) < len(header):
            updated_row.append('')
        
        # Обновляем только те колонки, которые указаны в item_data и существуют в заголовках
        updated_fields = []
        for col_name, col_value in item_data.items():
            if col_name == lookup_field:
                # Поле поиска не обновляем
                continue
            
            col_idx = header_to_idx.get(col_name.strip())
            if col_idx is not None:
                # Обновляем значение по реальному индексу колонки в листе
                updated_row[col_idx] = col_value if col_value is not None else ''
                updated_fields.append(col_name)
            else:
                logger.debug(f"Колонка '{col_name}' не найдена в заголовках листа {sheet_name}, пропускаем")
        
        # Обновляем строку в Google Sheets (только обновленные колонки)
        # Используем batch_update для обновления только нужных ячеек
        updates = []
        for col_name in updated_fields:
            col_idx = header_to_idx.get(col_name.strip())
            if col_idx is not None:
                from gspread.utils import rowcol_to_a1
                cell_address = rowcol_to_a1(row_num, col_idx + 1)  # gspread использует 1-based индексы
                updates.append({
                    'range': cell_address,
                    'values': [[item_data[col_name] if item_data[col_name] is not None else '']]
                })
        
        if updates:
            ws.batch_update(updates, value_input_option='RAW')
            logger.info(f"Обновлена строка {row_num} в листе {sheet_name} по {lookup_field}={lookup_value}. Обновлены поля: {', '.join(updated_fields)}")
        else:
            logger.warning(f"Нет полей для обновления в строке {row_num}")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обновления строки в листе {sheet_name}: {e}", exc_info=True)
        return False


async def delete_sheet_row(doc, sheet_name, lookup_value, lookup_field='checksum'):
    """
    Удаляет строку из Google Sheets по checksum или id.
    :param doc: gspread документ
    :param sheet_name: имя листа
    :param lookup_value: значение для поиска (checksum или id)
    :param lookup_field: поле для поиска строки ('checksum' или 'id')
    :return: True если успешно удалено, False в случае ошибки
    """
    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return False
    
    try:
        all_values = ws.get_all_values()
        if not all_values or len(all_values) < 2:
            logger.debug(f"Лист {sheet_name} пуст или содержит только заголовки - удаление невозможно")
            return False
        
        header = all_values[0]
        
        # Находим индекс поля для поиска
        try:
            lookup_idx = header.index(lookup_field)
        except ValueError:
            logger.error(f"Поле {lookup_field} не найдено в заголовках листа {sheet_name}")
            return False
        
        if not lookup_value:
            logger.warning(f"Значение {lookup_field} отсутствует")
            return False
        
        # Ищем строку для удаления
        row_num = None
        for idx, row in enumerate(all_values[1:], start=2):  # Начинаем с 2 (первая строка - заголовок)
            if len(row) > lookup_idx and row[lookup_idx] == str(lookup_value):
                row_num = idx
                break
        
        if not row_num:
            logger.debug(f"Строка с {lookup_field}={lookup_value} не найдена в листе {sheet_name}")
            return False
        
        # Удаляем строку из Google Sheets
        ws.delete_rows(row_num)
        logger.info(f"Удалена строка {row_num} из листа {sheet_name} по {lookup_field}={lookup_value}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка удаления строки из листа {sheet_name}: {e}", exc_info=True)
        return False


async def find_and_remove_duplicates_in_sheets(doc, sheet_name='raw_feed', repository=None):
    """
    Находит и удаляет дубликаты в Google Sheets по содержимому (raw_title, raw_content, raw_html).
    Для каждого найденного дубликата:
    1. Удаляет его из Google Sheets
    2. Помечает в БД как удалённый (статус 'DISCARDED'), если repository передан
    
    :param doc: gspread документ
    :param sheet_name: имя листа
    :param repository: экземпляр Repository для пометки в БД (опционально)
    :return: словарь со статистикой {'found': количество найденных дубликатов, 'removed': количество удалённых}
    """
    stats = {'found': 0, 'removed': 0, 'marked': 0, 'errors': 0}
    
    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return stats
    
    try:
        all_values = ws.get_all_values()
        if not all_values or len(all_values) < 2:
            logger.debug(f"Лист {sheet_name} пуст или содержит только заголовки - дубликаты не найдены")
            return stats
        
        header = all_values[0]
        
        # Находим индексы нужных полей
        try:
            raw_title_idx = header.index('raw_title')
            raw_content_idx = header.index('raw_content')
            raw_html_idx = header.index('raw_html') if 'raw_html' in header else None
            id_idx = header.index('id') if 'id' in header else None
            checksum_idx = header.index('checksum') if 'checksum' in header else None
        except ValueError as e:
            logger.error(f"Не найдены необходимые поля в заголовках листа {sheet_name}: {e}")
            return stats
        
        # Словарь для отслеживания содержимого: ключ = (raw_title, raw_content, raw_html), значение = список (row_num, id, checksum)
        content_map = {}
        
        # Проходим по всем строкам и группируем по содержимому
        for idx, row in enumerate(all_values[1:], start=2):
            if len(row) <= max(raw_title_idx, raw_content_idx):
                continue
            
            raw_title = (row[raw_title_idx] if len(row) > raw_title_idx else '').strip()
            raw_content = (row[raw_content_idx] if len(row) > raw_content_idx else '').strip()
            raw_html = (row[raw_html_idx] if raw_html_idx and len(row) > raw_html_idx else '').strip()
            
            # Пропускаем пустые строки
            if not raw_title and not raw_content and not raw_html:
                continue
            
            content_key = (raw_title, raw_content, raw_html)
            item_id = row[id_idx] if id_idx and len(row) > id_idx else None
            checksum = row[checksum_idx] if checksum_idx and len(row) > checksum_idx else None
            
            if content_key not in content_map:
                content_map[content_key] = []
            content_map[content_key].append({
                'row_num': idx,
                'id': item_id,
                'checksum': checksum
            })
        
        # Находим дубликаты (группы с более чем одной записью)
        duplicates_to_remove = []
        for content_key, items in content_map.items():
            if len(items) > 1:
                stats['found'] += len(items) - 1  # Все кроме первой считаются дубликатами
                
                # Сортируем по row_num (первая запись остаётся, остальные удаляем)
                items_sorted = sorted(items, key=lambda x: x['row_num'])
                
                # Оставляем первую запись, остальные помечаем на удаление
                for item in items_sorted[1:]:
                    duplicates_to_remove.append({
                        'row_num': item['row_num'],
                        'id': item['id'],
                        'checksum': item['checksum'],
                        'content': content_key
                    })
        
        # Удаляем дубликаты (в обратном порядке, чтобы индексы не сбились)
        duplicates_to_remove.sort(key=lambda x: x['row_num'], reverse=True)
        
        for dup in duplicates_to_remove:
            try:
                # Удаляем из Google Sheets
                ws.delete_rows(dup['row_num'])
                stats['removed'] += 1
                logger.info(f"Удалён дубликат из строки {dup['row_num']} в листе {sheet_name} (id={dup['id']}, checksum={dup['checksum']})")
                
                # Помечаем в БД как удалённый, если repository передан и есть id
                if repository and dup['id']:
                    try:
                        item_id = int(dup['id'])
                        if await repository.mark_item_as_discarded(item_id, reason='Дубликат по содержимому (raw_title + raw_content + raw_html)'):
                            stats['marked'] += 1
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Не удалось преобразовать id '{dup['id']}' в число: {e}")
                
            except Exception as e:
                stats['errors'] += 1
                logger.error(f"Ошибка удаления дубликата из строки {dup['row_num']}: {e}")
        
        logger.info(f"Обработка дубликатов завершена: найдено {stats['found']}, удалено {stats['removed']}, помечено в БД {stats['marked']}, ошибок {stats['errors']}")
        return stats
        
    except Exception as e:
        logger.error(f"Ошибка поиска и удаления дубликатов в листе {sheet_name}: {e}", exc_info=True)
        stats['errors'] += 1
        return stats


async def save_to_sheet(doc, sheet_name, data_list, existing_checksums=None, ws_cache=None):
    """
    Универсальная функция для записи данных в указанный лист Google Sheets.
    :param doc: gspread документ
    :param sheet_name: имя листа
    :param data_list: список словарей (каждый — строка)
    :param existing_checksums: множество существующих checksum (опционально, для оптимизации)
    :param ws_cache: кэш worksheet (опционально, для оптимизации)
    """
    columns = SHEET_COLUMNS.get(sheet_name)
    if not columns:
        logger.error(f"Нет структуры столбцов для листа {sheet_name}")
        return
    ws = ws_cache if ws_cache else get_worksheet(doc, sheet_name)
    if ws is None:
        return
    
    # Получаем существующие checksum из листа (столбец "checksum")
    # Используем переданный existing_checksums или получаем из листа
    if existing_checksums is None:
        try:
            # Проверка и добавление заголовков, если лист пустой
            all_values = ws.get_all_values()
            if not all_values:
                ws.append_row(columns)
                existing_checksums = set()
            elif all_values and len(all_values) > 1:
                header = all_values[0]
                try:
                    checksum_idx = header.index("checksum")
                except ValueError:
                    checksum_idx = None
                existing_checksums = set()
                if checksum_idx is not None:
                    for row in all_values[1:]:
                        if len(row) > checksum_idx and row[checksum_idx]:
                            existing_checksums.add(row[checksum_idx])
            else:
                existing_checksums = set()
        except Exception as e:
            logger.error(f"Ошибка получения существующих checksum: {e}")
            existing_checksums = set()
    else:
        # Если existing_checksums передан, просто используем его
        # Проверяем, есть ли заголовки (минимальная проверка без get_all_values)
        try:
            # Пытаемся прочитать только первую строку для проверки заголовков
            header_row = ws.row_values(1)
            if not header_row:
                ws.append_row(columns)
        except Exception:
            # Если не удалось прочитать, предполагаем что заголовки есть
            pass
    # HEADER-BASED: Читаем реальные заголовки из листа
    try:
        all_values = ws.get_all_values()
        if not all_values:
            # Если лист пустой, создаем заголовки из схемы
            ws.append_row(columns)
            header = columns
        else:
            header = all_values[0]
    except Exception as e:
        logger.error(f"Ошибка чтения заголовков листа {sheet_name}: {e}")
        return
    
    # Строим маппинг header -> индекс для быстрого поиска
    header_to_idx = {col_name.strip(): idx for idx, col_name in enumerate(header) if col_name and col_name.strip()}

    # P0: row_number обязателен для безопасного writeback сайта
    if sheet_name == "raw_feed" and "row_number" not in header_to_idx:
        raise RuntimeError(
            "P0: В листе raw_feed отсутствует колонка 'row_number' в заголовках. "
            "Остановка записи, чтобы сайт не сделал небезопасный writeback."
        )
    
    # Преобразуем данные в нужный порядок (header-based)
    valid_rows = []
    seen_checksums = set(existing_checksums)  # Для фильтрации дублей внутри одной итерации
    
    # Следующий номер строки для вставки (1-based, учитывая строку заголовков)
    next_row_number = (len(all_values) + 1) if all_values else 2

    for item in data_list:
        if 'checksum' not in item or not item['checksum']:
            item['checksum'] = generate_checksum(item)
        if 'id' not in item or not item['id']:
            item['id'] = item['checksum']

        # P0: header-based + DEFAULTS — заполняем отсутствующие значения дефолтами по именам колонок
        for col_name in header:
            if not col_name:
                continue
            if col_name not in item or item[col_name] is None or (isinstance(item[col_name], str) and not item[col_name].strip()):
                item[col_name] = DEFAULTS.get(col_name, "")

        # Гарантируем критичные поля времени/попыток (если пусто)
        if not item.get("created_at"):
            item["created_at"] = datetime.now(timezone.utc).isoformat()
        if not item.get("updated_at"):
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not item.get("ingest_last_try_at"):
            item["ingest_last_try_at"] = datetime.now(timezone.utc).isoformat()
        if not item.get("ingest_attempts"):
            item["ingest_attempts"] = 1

        if validate_raw_row(item) and item['checksum'] not in seen_checksums:
            # P0: Проставляем row_number ДО вставки (детерминированно)
            if sheet_name == "raw_feed":
                item_row_number = next_row_number + len(valid_rows)
                item["row_number"] = str(item_row_number)

            # HEADER-BASED: Формируем row строго по текущим заголовкам листа
            # (порядок колонок в таблице не важен)
            row = []
            for col_name in header:
                if not col_name:
                    row.append("")
                    continue
                value = item.get(col_name, DEFAULTS.get(col_name, ""))
                row.append("" if value is None else value)

            # P0: Если row_number не удалось проставить — запрещаем запись
            if sheet_name == "raw_feed":
                rn_idx = header_to_idx.get("row_number")
                if rn_idx is None or not str(row[rn_idx]).strip():
                    raise RuntimeError(
                        "P0: Не удалось определить/записать row_number для новой строки raw_feed. "
                        "Остановка записи, чтобы сайт не сделал небезопасный writeback."
                    )

            valid_rows.append(row)
            seen_checksums.add(item['checksum'])
    
    if valid_rows:
        logger.info(f"Добавление {len(valid_rows)} строк в лист {sheet_name} (header-based)")
        # ВАЖНО: gspread append_rows возвращает ответ API с диапазоном вставки.
        # Для raw_feed это критично: row_number должен соответствовать реальному номеру строки.
        resp = ws.append_rows(valid_rows, value_input_option="RAW")

        # P0: гарантируем корректный row_number после реальной вставки
        if sheet_name == "raw_feed":
            try:
                # Ожидаемые варианты структуры ответа:
                # - {"updates": {"updatedRange": "raw_feed!A198:BP198", ...}, ...}
                # - {"tableRange": "...", "updates": {"updatedRange": "..."}}
                updated_range = None
                if isinstance(resp, dict):
                    updates_obj = resp.get("updates") if resp else None
                    if isinstance(updates_obj, dict):
                        updated_range = updates_obj.get("updatedRange")

                if not updated_range or "!" not in updated_range:
                    raise RuntimeError(f"append_rows не вернул updatedRange (resp keys={list(resp.keys()) if isinstance(resp, dict) else type(resp)})")

                # Парсим начальную строку из формата A198:BP198
                import re
                a1_range = updated_range.split("!", 1)[1]
                start_a1 = a1_range.split(":", 1)[0]
                m = re.search(r"(\d+)$", start_a1)
                if not m:
                    raise RuntimeError(f"Не удалось распарсить номер строки из updatedRange={updated_range}")
                start_row_num = int(m.group(1))

                rn_col_idx = header_to_idx.get("row_number")
                if rn_col_idx is None:
                    raise RuntimeError("P0: row_number отсутствует в header_to_idx после вставки")

                updates = []
                from gspread.utils import rowcol_to_a1
                for i in range(len(valid_rows)):
                    real_row_num = start_row_num + i
                    cell = rowcol_to_a1(real_row_num, rn_col_idx + 1)
                    updates.append({"range": cell, "values": [[str(real_row_num)]]})

                if not updates:
                    raise RuntimeError("P0: пустой updates для row_number")

                ws.batch_update(updates, value_input_option="RAW")
            except Exception as e:
                raise RuntimeError(
                    f"P0: вставка выполнена, но не удалось гарантировать корректный row_number по реальному диапазону вставки: {e}"
                )

        logger.info(f"Добавлено {len(valid_rows)} строк в лист {sheet_name}")
    else:
        logger.warning(f"Нет валидных строк для записи в {sheet_name} (или все дубли)")

# --- Структуры столбцов для каждого листа ---
SHEET_COLUMNS = {
    # Каноничная схема raw_feed (68 колонок) — единый источник истины: utils/sheet_schema.py
    'raw_feed': RAW_FEED_COLUMNS,
    'posts': [
        "id", "final_text", "hashtags", "cta", "published_date", "status", "link_vk", "link_tg", "link_fb", "link_dzen", "post_type", "author_id"
    ],
    'events': [
        "timestamp", "event_type", "object_id", "description", "result", "extra"
    ],
    'user_messages': [
        "message_id", "user_id", "user_name", "related_id", "text", "message_type", "timestamp", "status"
    ],
    'analytics': [
        "date", "news_processed", "posts_published", "user_feedback_count", "avg_time_to_publish", "errors_count", "users_active", "reach_vk", "reach_tg", "reach_fb", "reach_dzen"
    ]
}

def get_worksheet(doc, sheet_name):
    try:
        return doc.worksheet(sheet_name)
    except Exception as e:
        logger.error(f"Не найден лист {sheet_name}: {e}")
        return None

def validate_sheet_headers(header_row: list, sheet_name: str = 'raw_feed'):
    """
    Валидация заголовков листа: проверка на дубликаты (fail-fast).
    Игнорирует значения, которые явно не являются названиями колонок (числа, булевы значения).
    
    :param header_row: список заголовков
    :param sheet_name: имя листа для логирования
    :return: (is_valid, duplicates_list) - валидность и список дубликатов
    """
    if not header_row:
        return True, []
    
    # Нормализуем заголовки (убираем пробелы, приводим к нижнему регистру для сравнения)
    normalized_headers = {}
    duplicates = []
    
    def is_likely_column_name(value: str) -> bool:
        """
        Проверяет, похоже ли значение на название колонки (а не на данные новостей).
        Игнорирует: только числа, булевы значения (TRUE/FALSE), очень короткие строки без букв,
        длинные тексты (данные новостей), строки с переносами строк.
        
        Названия колонок обычно:
        - Короткие (до 50-100 символов)
        - Без переносов строк
        - Без множественных предложений
        - Могут быть в списке ожидаемых колонок RAW_FEED_COLUMNS
        """
        if not value or not value.strip():
            return False
        
        value_stripped = value.strip()
        value_lower = value_stripped.lower()
        
        # Игнорируем булевы значения
        if value_lower in ('true', 'false', '1', '0', 'yes', 'no', 'да', 'нет'):
            return False
        
        # Игнорируем только числа (без букв)
        if value_lower.isdigit():
            return False
        
        # Игнорируем очень короткие строки без букв (меньше 2 символов)
        if len(value_lower) < 2:
            return False
        
        # Игнорируем строки, которые состоят только из цифр и знаков препинания
        if not any(c.isalpha() for c in value_lower):
            return False
        
        # КРИТИЧНО: Игнорируем длинные тексты - это данные новостей, а не названия колонок
        # Названия колонок обычно не превышают 100 символов
        if len(value_stripped) > 100:
            return False
        
        # КРИТИЧНО: Игнорируем строки с переносами строк - это данные новостей
        # Названия колонок не содержат переносов строк
        if '\n' in value_stripped or '\r' in value_stripped:
            return False
        
        # КРИТИЧНО: Игнорируем строки с множественными предложениями (много точек)
        # Названия колонок обычно одно слово или короткая фраза без множества точек
        sentence_count = value_stripped.count('.') + value_stripped.count('!') + value_stripped.count('?')
        if sentence_count > 2:  # Если больше 2 предложений - это явно данные новостей
            return False
        
        # Дополнительная проверка: если строка содержит много пробелов (много слов),
        # это может быть данными новостей, но некоторые колонки могут быть многословными
        # Поэтому проверяем только очень длинные фразы (более 10 слов)
        word_count = len(value_stripped.split())
        if word_count > 10:  # Если больше 10 слов - это явно данные новостей
            return False
        
        # Если значение есть в списке ожидаемых колонок - это точно название колонки
        if value_stripped in RAW_FEED_COLUMNS:
            return True

        # P0: Ужесточаем распознавание, чтобы мусор/контент в строке заголовков
        # (например, заголовки новостей) не ломали запись.
        # Разрешаем только snake_case латиницей (как принято в таблице).
        import re
        if re.match(r"^[a-z][a-z0-9_]{0,63}$", value_lower):
            return True

        # Всё остальное считаем "не заголовком" (данные/мусор) и игнорируем
        return False
    
    for idx, header in enumerate(header_row):
        if not header or not header.strip():
            continue
        
        # Пропускаем значения, которые явно не являются названиями колонок
        if not is_likely_column_name(header):
            continue
        
        # Нормализуем имя заголовка (убираем пробелы, приводим к нижнему регистру)
        normalized = header.strip().lower()
        
        # Проверяем дубликаты
        if normalized in normalized_headers:
            # Найден дубликат!
            original_idx = normalized_headers[normalized]['index']
            original_name = normalized_headers[normalized]['name']
            duplicates.append({
                'normalized': normalized,
                'first_occurrence': {'index': original_idx, 'name': original_name},
                'duplicate': {'index': idx, 'name': header.strip()}
            })
        else:
            normalized_headers[normalized] = {'index': idx, 'name': header.strip()}
    
    return len(duplicates) == 0, duplicates


async def ensure_sheet_headers(doc, sheet_name='raw_feed'):
    """
    Автоприведение заголовков листа к схеме с валидацией на дубликаты (fail-fast).
    Проверяет заголовки листа и дописывает недостающие колонки из RAW_FEED_COLUMNS.
    
    :param doc: gspread документ
    :param sheet_name: имя листа (по умолчанию 'raw_feed')
    :return: True если успешно, False в случае ошибки
    :raises: ValueError при обнаружении дубликатов заголовков (fail-fast)
    """
    try:
        ws = get_worksheet(doc, sheet_name)
        if ws is None:
            logger.error(f"Не удалось открыть лист {sheet_name}")
            return False
        
        # Читаем текущие заголовки
        try:
            header_row = ws.row_values(1)
        except Exception as e:
            logger.warning(f"Не удалось прочитать заголовки листа {sheet_name}: {e}")
            header_row = []
        
        # Если лист пустой, создаем заголовки полностью
        if not header_row:
            ws.append_row(RAW_FEED_COLUMNS)
            logger.info(f"Созданы заголовки для пустого листа {sheet_name}: {len(RAW_FEED_COLUMNS)} колонок")
            return True
        
        # КРИТИЧНО: Валидация заголовков на дубликаты (fail-fast)
        is_valid, duplicates = validate_sheet_headers(header_row, sheet_name)
        if not is_valid:
            error_msg = f"КРИТИЧЕСКАЯ ОШИБКА: Обнаружены дубликаты заголовков в листе {sheet_name}:\n"
            for dup in duplicates:
                error_msg += f"  - '{dup['normalized']}' найден дважды:\n"
                error_msg += f"    Первое вхождение: колонка {dup['first_occurrence']['index']+1} ('{dup['first_occurrence']['name']}')\n"
                error_msg += f"    Дубликат: колонка {dup['duplicate']['index']+1} ('{dup['duplicate']['name']}')\n"
            error_msg += "\nДЕЙСТВИЕ: Исправьте таблицу вручную (удалите/переименуйте дубликаты), затем перезапустите парсер."
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.debug(f"Валидация заголовков листа {sheet_name} пройдена: дубликатов не обнаружено")
        
        # Находим недостающие колонки
        missing_columns = [col for col in RAW_FEED_COLUMNS if col not in header_row]
        
        if missing_columns:
            # Дописываем недостающие колонки в конец первой строки
            # Для этого нужно обновить первую строку, добавив новые колонки
            updated_header = header_row + missing_columns
            # Обновляем первую строку полностью (gspread требует указать диапазон)
            # Находим последний столбец с новым заголовком
            from gspread.utils import rowcol_to_a1
            new_last_col_letter = rowcol_to_a1(1, len(updated_header))
            range_name = f"A1:{new_last_col_letter}"
            ws.update(range_name, [updated_header], value_input_option='RAW')
            logger.info(f"Добавлено {len(missing_columns)} недостающих колонок в лист {sheet_name}: {', '.join(missing_columns)}")
        else:
            logger.debug(f"Все колонки схемы присутствуют в листе {sheet_name}")
        
        return True
    except ValueError:
        # Перебрасываем ValueError (fail-fast)
        raise
    except Exception as e:
        logger.error(f"Ошибка при автоприведении заголовков листа {sheet_name}: {e}", exc_info=True)
        return False

async def parse_single_url(url: str, source_type: str = None, date_from=None, date_to=None) -> List[Dict]:
    """
    Парсит конкретную ссылку (не источник, а одну ссылку).
    Полезно для парсинга конкретной новости/поста по ссылке.
    
    Args:
        url: URL для парсинга
        source_type: Тип источника ('rss', 'telegram', 'youtube', 'website'). Если None, определяется автоматически
        date_from: Начальная дата для фильтрации (datetime, опционально)
        date_to: Конечная дата для фильтрации (datetime, опционально)
    
    Returns:
        Список словарей с данными новостей (обычно 1 элемент)
    """
    from storage.sources import NewsSource
    
    # Определяем тип источника автоматически, если не указан
    if not source_type:
        url_lower = url.lower()
        if 't.me' in url_lower or url_lower.startswith('@'):
            source_type = 'telegram'
        elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            source_type = 'youtube'
        elif url_lower.endswith('.xml') or 'feed' in url_lower or 'rss' in url_lower:
            source_type = 'rss'
        else:
            source_type = 'website'
    
    # Создаем временный источник для парсинга
    temp_source = NewsSource(
        type=source_type,
        url=url,
        name=f"Temp source for {url}",
        filter=False
    )
    
    # Получаем парсер
    parser = None
    if source_type == 'telegram':
        # Для Telegram нужен клиент
        try:
            from telegram_session import TelegramSessionManager
            from config.settings import config
            session_manager = TelegramSessionManager(
                config.TELEGRAM_API_ID_USER,
                config.TELEGRAM_API_HASH_USER,
                config.TELEGRAM_PHONE
            )
            tg_client = await session_manager.get_client()
            if tg_client:
                parser = TelegramParser(tg_client, limit=1)
        except Exception as e:
            logger.error(f"Не удалось создать Telegram парсер: {e}")
            return []
    elif source_type == 'rss':
        parser = RSSParser(limit=1)
    elif source_type == 'youtube':
        from config.settings import config
        if config.YOUTUBE_API_KEY:
            parser = YoutubeParser(config.YOUTUBE_API_KEY, limit=1)
        else:
            logger.error("YOUTUBE_API_KEY не настроен")
            return []
    elif source_type == 'website':
        parser = WebsiteParser(limit=1)
    
    if not parser:
        logger.error(f"Не удалось создать парсер для типа {source_type}")
        return []
    
    # Парсим
    try:
        results = await parser.parse(temp_source, date_from=date_from, date_to=date_to)
        return results if results else []
    except Exception as e:
        logger.error(f"Ошибка парсинга ссылки {url}: {e}")
        return []


async def auto_save_to_sheets(doc, all_data: dict):
    """
    Автоматически сохраняет данные в нужные листы Google Sheets по ключам all_data.
    Ожидает структуру: {'raw_feed': [...], 'posts': [...], ...}
    """
    for sheet_name, data_list in all_data.items():
        if sheet_name in SHEET_COLUMNS and data_list:
            await save_to_sheet(doc, sheet_name, data_list)

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
        doc = await init_google_sheets()
        if not doc:
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
            # --- Сбор данных по типам ---
            all_data = {k: [] for k in SHEET_COLUMNS.keys()}
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
                    # По умолчанию всё в raw_feed, но если есть ключ 'sheet', используем его
                    if isinstance(result, list):
                        all_data['raw_feed'].extend(result)
                    elif isinstance(result, dict):
                        for k, v in result.items():
                            if k in all_data and isinstance(v, list):
                                all_data[k].extend(v)
            # --- Автоматическая запись во все листы ---
            await auto_save_to_sheets(doc, all_data)
            logger.info(f"Автоматически сохранено: {[f'{k}: {len(v)}' for k,v in all_data.items() if v]} записей")
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