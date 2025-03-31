import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse
from telethon import TelegramClient            # новый импорт
from telethon.errors import ChannelPrivateError  # новый импорт

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

@dataclass
class NewsSource:
    type: str  # 'telegram', 'rss', 'youtube', 'website'
    url: str   # Channel username, feed URL, website URL, etc.
    filter: bool = True
    last_id: Optional[str] = None
    name: str = ""


ALLOWED_SOURCE_TYPES = {"telegram", "rss", "youtube", "website"}
news_sources: List[NewsSource] = []

# Новый: асинхронная функция для проверки приватности telegram канала
async def is_channel_private(client: TelegramClient, channel_url: str) -> bool:
    """Проверка, является ли канал приватным."""
    try:
        entity = await client.get_entity(channel_url)
        return entity.username is None  # если username отсутствует, канал приватный
    except ChannelPrivateError:
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки канала: {e}")
        return False

# Изменённая асинхронная функция add_source с дополнительным параметром client
async def add_source(source_type: str, source_url: str, use_filter: bool = True, name: str = "", client: TelegramClient = None) -> Optional[NewsSource]:
    source_type = source_type.lower().strip()
    if source_type not in ALLOWED_SOURCE_TYPES:
        logger.warning(f"Попытка добавить недопустимый тип источника: {source_type}")
        return None

    if source_type == "telegram":
        source_url = source_url.strip().replace("t.me/", "").lstrip("@")
        source_url = f"https://t.me/{source_url}"
        if not client:
            logger.error("Для проверки приватности telegram требуется TelegramClient.")
            return None
        if await is_channel_private(client, source_url):
            logger.warning(f"Канал {source_url} является приватным и не может быть добавлен.")
            return None

    if any(src.type == source_type and src.url == source_url for src in news_sources):
        logger.info(f"Источник {source_url} ({source_type}) уже добавлен.")
        return None

    if not name:
        name = urlparse(source_url).netloc if source_type == "website" else source_url

    new_source = NewsSource(type=source_type, url=source_url, filter=use_filter, name=name)
    news_sources.append(new_source)
    logger.info(f"Добавлен источник: {new_source}")
    return new_source


def remove_source(source_type: str, source_url: str) -> bool:
    global news_sources
    for src in news_sources:
        if src.type == source_type and src.url == source_url:
            news_sources.remove(src)
            logger.info(f"Удалён источник: {src}")
            return True
    logger.warning(f"Источник {source_url} ({source_type}) не найден для удаления.")
    return False


def list_sources() -> List[NewsSource]:
    return news_sources


def load_sources():
    """
    Загружаем предустановленные источники.
    Старые + добавляем все, что вы перечислили:
    Websites, RSS, Telegram, YouTube.
    """
    global news_sources
    news_sources = [
        # Сохраняем старые
        NewsSource("rss", "https://www.wakeboardingmag.com/feed", True, None, "Wakeboarding Magazine (RSS)"),
        NewsSource("rss", "https://thewwa.com/feed", True, None, "WWA Blog (RSS)"),
        NewsSource("youtube", "https://www.youtube.com/channel/UCJluNGyCBXAR6-CHPRMrZUw", True, None, "JB O'Neill"),
        NewsSource("telegram", "https://t.me/talktofish", True, None, "Talk to Fish"),

        # ========== Новые веб-сайты ==========
        NewsSource("website", "https://www.wakeboardingmag.com", True, None, "Wakeboarding Magazine"),
        NewsSource("website", "https://alliancewake.com", True, None, "Alliance Wake"),
        NewsSource("website", "https://www.thewwa.com/blog", True, None, "World Wake Association Blog"),
        NewsSource("website", "https://unleashedwakemag.com/blog-unleashed-wake-mag/", True, None, "Unleashed Wake Magazine"),
        NewsSource("website", "https://blog.miamiskinautiques.com", True, None, "Miami Ski Nautique Blog"),

        # ========== Новые RSS-фиды ==========
        NewsSource("rss", "https://wakeboardingmag.com/feed", True, None, "WakeboardingMag.com/feed"),
        NewsSource("rss", "https://thewwa.com/feed", True, None, "TheWWA Feed"),  # дублируем, если нужно
        NewsSource("rss", "https://unleashedwakemag.com/feed", True, None, "Unleashed Wake Magazine RSS"),
        NewsSource("rss", "https://makeawakemarine.com/blogs/make-a-wake-marine-blog/feed", True, None, "Make A Wake Marine Blog"),
        NewsSource("rss", "https://blog.miamiskinautiques.com/feed", True, None, "Miami Ski Nautique Blog RSS"),

        # ========== Новые YouTube ==========
        NewsSource("youtube", "https://www.youtube.com/channel/UCEO3Li9O6BHE3SLR7f0WOGA", True, None, "Shaun Murray"),
        NewsSource("youtube", "https://www.youtube.com/channel/UCbc8Ap_hqExdpTd8YH4rw9A", True, None, "David O'Caoimh"),
        NewsSource("youtube", "https://www.youtube.com/channel/UCVpeKZf-T4Hxtmfd5Ik50tA", True, None, "IWWF World Cup"),

        # ========== Новые Telegram ==========
        NewsSource("telegram", "https://t.me/prowakesurf", True, None, "Pro Wakesurf"),
        NewsSource("telegram", "https://t.me/wakestyleclub", True, None, "WakeStyle Club"),
        NewsSource("telegram", "https://t.me/moscow_wakesurfing", True, None, "Moscow Wakesurfing"),
        NewsSource("telegram", "https://t.me/waketime_msk", True, None, "WakeTime MSK"),
        NewsSource("telegram", "https://t.me/wakediary", True, None, "WakeDiary"),
        NewsSource("telegram", "https://t.me/wakedivision", True, None, "WakeDivision"),
        NewsSource("telegram", "https://t.me/Privat_Wakesurfing", True, None, "Privat Wakesurfing"),
        NewsSource("telegram", "https://t.me/russian_waterski", True, None, "Russian Waterski"),
        NewsSource("telegram", "https://t.me/RFSurf", True, None, "RF Surf"),
        NewsSource("telegram", "https://t.me/surfinmoscow", True, None, "Surf in Moscow"),
        NewsSource("telegram", "https://t.me/atcc_russia", True, None, "ATCC Russia"),
        NewsSource("telegram", "https://t.me/surfmosobl", True, None, "Surf Moscow Region"),
        NewsSource("telegram", "https://t.me/s/wakeflot?after=571", True, None, "Wakeflot"),
    ]
    logger.info("Предустановленные источники загружены.")

# Загружаем при старте
load_sources()
