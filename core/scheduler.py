import asyncio
import schedule
import time
from collectors.telegram_parser import TelethonParser
from collectors.rss_parser import RSSParser
from collectors.youtube_parser import YoutubeParser
from collectors.website_parser import WebsiteParser
from storage.google_sheets import GoogleSheets
from config.settings import config
from telethon import TelegramClient
import logging
from storage.sources import list_sources
from utils.telegram_session import TelegramSessionManager
from data import save_news

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Инициализируем клиента Google Sheets
sheets = GoogleSheets()

async def parse_source(parser, source):
    """Парсинг одного источника."""
    try:
        logger.info(f"Начат парсинг источника: {source.url}")
        data = await parser.parse(source)
        if data:
            logger.info(f"Получено {len(data)} записей из {source.url}")
            return data
        else:
            logger.info(f"Нет данных из {source.url}")
            return []
    except Exception as e:
        logger.error(f"Ошибка при парсинге {source.url}: {e}")
        return []

async def parse_all_sources():
    """Парсинг всех источников."""
    logger.info("Запуск автоматического парсинга...")
    session_manager = TelegramSessionManager(config.TELEGRAM_API_ID_USER, config.TELEGRAM_API_HASH_USER, config.TELEGRAM_PHONE)
    parsers = {
        "telegram": TelethonParser(await session_manager.get_client()),
        "rss": RSSParser(),
        "website": WebsiteParser(),
        "youtube": YoutubeParser(config.YOUTUBE_API_KEY)
    }
    all_data = []
    tasks = []
    for source in list_sources():
        parser = parsers.get(source.type)
        if parser:
            tasks.append(parse_source(parser, source))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Ошибка при парсинге: {result}")
        elif result:
            all_data.extend(result)
            logger.info(f"Получено {len(result)} записей")
    
    if all_data:
        save_news(all_data)
        logger.info(f"Всего сохранено {len(all_data)} записей")
    
    logger.info("Парсинг завершён!")
    await session_manager.close_client()

def run_scheduler():
    """Запуск планировщика."""
    interval = config.PARSING_INTERVAL / 3600 # Default to 4 hours
    schedule.every(interval).hours.do(lambda: asyncio.run(parse_all_sources()))
    logger.info(f"Планировщик запущен с интервалом {interval} часа(ов).")
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")

if __name__ == "__main__":
    run_scheduler()
