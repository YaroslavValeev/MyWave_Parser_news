"""Устойчивый entrypoint редакторского Telegram-бота (aiogram v3)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import config
from services.scheduler import SchedulerService
from storage.data import get_repository

LOGGER = logging.getLogger(__name__)


async def _review_notifier(bot: Bot, repository) -> None:
    """Периодически шлёт карточки review, если репозиторий поддерживает нужный API."""
    if not hasattr(repository, "list_items_by_status"):
        LOGGER.warning("Репозиторий не поддерживает list_items_by_status; review_notifier отключён")
        return
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
            item_id = item.get("id")
            if not item_id or item_id in seen:
                continue
            title = item.get("title") or "Без заголовка"
            await bot.send_message(chat_id, f"📝 <b>Review</b>\n{title}")
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

    @dispatcher.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer("Бот запущен ✅")

    scheduler_service = SchedulerService(repository, bot)
    notifier_task = None
    try:
        await scheduler_service.start()
    except Exception:  # noqa: BLE001
        LOGGER.exception("Не удалось запустить SchedulerService; продолжаем без планировщика")
    else:
        notifier_task = asyncio.create_task(_review_notifier(bot, repository))

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        if notifier_task is not None:
            notifier_task.cancel()
            with suppress(asyncio.CancelledError):
                await notifier_task
        with suppress(Exception):
            await scheduler_service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
