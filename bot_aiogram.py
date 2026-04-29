"""Точка входа для редакторского Telegram-бота на aiogram."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config.settings import config
from storage.data import get_repository
from storage.repository import AsyncNewsRepository
from telegram_bot import create_router
from telegram_bot.keyboards import review_keyboard
from telegram_bot.middlewares import AccessLogMiddleware, RepositoryMiddleware
from telegram_bot.views import format_item_card
from services.scheduler import SchedulerService

LOGGER = logging.getLogger(__name__)


async def review_notifier(bot: Bot, repository: AsyncNewsRepository) -> None:
    """Рассылает новые карточки в чат редакторов."""

    if not config.EDITORS_CHAT_ID:
        LOGGER.warning("EDITORS_CHAT_ID не задан, уведомления отключены")
        return

    chat_id: Optional[int | str]
    try:
        chat_id = int(config.EDITORS_CHAT_ID)
    except (TypeError, ValueError):
        chat_id = config.EDITORS_CHAT_ID

    seen: set[int] = set()
    while True:
        items = await repository.list_items_by_status(["review"], limit=20)
        for item in items:
            item_id = item["id"]
            if item_id in seen:
                continue
            nlp = await repository.get_nlp_results(item_id)
            await bot.send_message(
                chat_id,
                format_item_card(item, nlp),
                reply_markup=review_keyboard(item_id, include_publish=True),
            )
            seen.add(item_id)
        await asyncio.sleep(30)


async def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в окружении")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repository = await get_repository()
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(RepositoryMiddleware(repository))
    dispatcher.message.outer_middleware(AccessLogMiddleware())
    dispatcher.callback_query.outer_middleware(AccessLogMiddleware())
    dispatcher.include_router(create_router())

    scheduler_service = SchedulerService(repository, bot)
    await scheduler_service.start()
    notifier_task = asyncio.create_task(review_notifier(bot, repository))

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        notifier_task.cancel()
        with suppress(asyncio.CancelledError):
            await notifier_task
        await scheduler_service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
