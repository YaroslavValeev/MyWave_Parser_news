import asyncio
import logging
import random
from telegram.ext import ContextTypes
from telethon.errors import FloodWaitError
from telegram_rate_limiter import RateLimiter  # Предполагается, что этот класс доступен в проекте

from config.settings import config
from utils.logger import logger
from bot import collect_news
from publishers.report_generator import generate_report
from storage.google_sheets import GoogleSheets

# Инициализация RateLimiter с ограничением 10 запросов в минуту
rate_limiter = RateLimiter(requests_per_minute=10)

class ParsingManager:
    """
    Класс для управления процессом парсинга новостей, их сохранения, генерации отчётов и уведомлений.
    """
    def __init__(self, sheets_client: GoogleSheets):
        """
        Инициализация менеджера парсинга с зависимостью от GoogleSheets клиента.

        Args:
            sheets_client (GoogleSheets): Клиент для работы с Google Sheets.
        """
        self.sheets_client = sheets_client

    async def fetch_news(self, context: ContextTypes.DEFAULT_TYPE) -> list:
        """
        Сбор новостей с обработкой ошибок FloodWaitError и общих исключений.

        Args:
            context (ContextTypes.DEFAULT_TYPE): Контекст Telegram бота.

        Returns:
            list: Список новостей или пустой список в случае ошибки.
        """
        try:
            logger.info("Запуск автоматического парсинга новостей...")
            news_items = await collect_news(context)
            if not news_items:
                logger.info("Нет новых новостей при автоматическом парсинге.")
            return news_items
        except FloodWaitError as e:
            logger.warning(f"Ожидание {e.seconds} секунд из-за FloodWaitError...")
            await asyncio.sleep(e.seconds)
            return []
        except Exception as e:
            logger.error(f"Ошибка во время парсинга: {e}", exc_info=True)
            return []

    async def save_news(self, news_items: list):
        """
        Сохранение новостей в Google Sheets с фильтрацией дубликатов и повторными попытками.

        Args:
            news_items (list): Список новостей для сохранения.
        """
        if not news_items:
            logger.info("Нет новостей для сохранения.")
            return

        rows = [
            [
                item.get('date', ''),
                item.get('source', ''),
                item.get('title', ''),
                item.get('content', ''),
                item.get('link', ''),
                "\n".join(item.get('images', [])),
                "\n".join(item.get('videos', [])),
            ]
            for item in news_items
        ]
        existing_ids = self.sheets_client.get_existing_news_ids()
        new_rows = [row for row in rows if row[0] not in existing_ids]
        if not new_rows:
            logger.info("Все новости уже существуют в Google Sheets.")
            return

        saved = False
        for attempt in range(1, 4):
            try:
                self.sheets_client.append_news(new_rows)
                logger.info(f"Автоматически сохранено новостей: {len(new_rows)} (попытка {attempt})")
                saved = True
                break
            except Exception as e:
                logger.error(f"Ошибка сохранения в Google Sheets на попытке {attempt}: {e}")
                await asyncio.sleep(2)
        if not saved:
            logger.error("Не удалось сохранить новости после 3 попыток.")

    async def generate_report(self, news_items: list) -> str:
        """
        Генерация отчёта по новостям, если они есть.

        Args:
            news_items (list): Список новостей для генерации отчёта.

        Returns:
            str: Путь к файлу отчёта или None в случае ошибки.
        """
        if not news_items:
            logger.info("Нет новостей для генерации отчёта.")
            return None
        try:
            report_file = generate_report(news_items)
            if report_file:
                logger.info(f"Отчёт успешно создан: {report_file}")
            return report_file
        except Exception as e:
            logger.error(f"Ошибка генерации отчёта: {e}", exc_info=True)
            return None

    async def notify_admin(self, context: ContextTypes.DEFAULT_TYPE, message: str):
        """
        Отправка уведомления администратору с повторными попытками, rate limiting и обработкой FloodWaitError.

        Args:
            context (ContextTypes.DEFAULT_TYPE): Контекст Telegram бота.
            message (str): Сообщение для отправки администратору.
        """
        admin_id = config.ADMIN_USER_ID
        if not admin_id:
            logger.warning("ADMIN_USER_ID не указан в конфигурации.")
            return
        for attempt in range(1, 4):
            try:
                # Ожидание разрешения от RateLimiter
                await rate_limiter.acquire()
                await context.bot.send_message(chat_id=admin_id, text=message)
                logger.info(f"Уведомление отправлено администратору (попытка {attempt})")
                break
            except FloodWaitError as e:
                logger.warning(f"FloodWaitError: ожидание {e.seconds} секунд")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления администратору на попытке {attempt}: {e}")
                if attempt < 3:
                    # Случайная задержка от 1 до 3 секунд перед повторной попыткой
                    await asyncio.sleep(random.uniform(1, 3))
                else:
                    logger.error("Не удалось отправить уведомление после 3 попыток.")

    async def manage_parsing(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Управление процессом парсинга: сбор, сохранение, генерация отчёта и уведомление с случайными задержками.

        Args:
            context (ContextTypes.DEFAULT_TYPE): Контекст Telegram бота.
        """
        # Случайная задержка перед началом парсинга (1-5 секунд)
        await asyncio.sleep(random.uniform(1, 5))
        news_items = await self.fetch_news(context)
        if news_items:
            # Случайная задержка перед сохранением
            await asyncio.sleep(random.uniform(1, 3))
            await self.save_news(news_items)
            # Случайная задержка перед генерацией отчёта
            await asyncio.sleep(random.uniform(1, 3))
            await self.generate_report(news_items)
            # Случайная задержка перед отправкой уведомления
            await asyncio.sleep(random.uniform(1, 3))
            await self.notify_admin(context, f"✅ Авто-парсинг завершён. Новостей сохранено: {len(news_items)}")

# Инициализируем клиента Google Sheets
sheets_client = GoogleSheets()

async def scheduled_parse(context: ContextTypes.DEFAULT_TYPE):
    """
    Автоматический парсинг новостей по расписанию.

    Args:
        context (ContextTypes.DEFAULT_TYPE): Контекст Telegram бота.
    """
    parsing_manager = ParsingManager(sheets_client)
    await parsing_manager.manage_parsing(context)

def setup_scheduled_tasks(job_queue):
    """
    Настроить задачи для периодического выполнения.

    Args:
        job_queue: Очередь задач Telegram бота.
    """
    job_queue.run_repeating(scheduled_parse, interval=48 * 60 * 60, first=5)
    logger.info("Запущен планировщик задач (парсинг раз в 48 часов).")