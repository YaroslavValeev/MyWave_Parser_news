from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config.settings import config
from .middlewares import RoleMiddleware
from .views import show_review_item, handle_callback
from storage.repository import AsyncNewsRepository, initialize_database


async def create_router(bot: Bot | None = None) -> Dispatcher:
    if bot is None:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    dp = Dispatcher()
    dp.message.middleware(RoleMiddleware())
    dp.callback_query.middleware(RoleMiddleware())

    repo = await initialize_database(config.DB_PATH)

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        await message.answer("Бот редакции запущен.")

    @dp.message(Command("review"))
    async def cmd_review(message: Message):
        # Показать последнюю заявку
        items = await repo.list_items_by_status("new")
        if not items:
            await message.answer("Нет новых материалов")
            return
        item = items[0]
        await show_review_item(repo, item["id"], message.chat)

    @dp.callback_query()
    async def on_callback(query: CallbackQuery):
        data = query.data or ""
        # Наивный разбор — в prod лучше использовать callback_data.unpack
        # но тут мы полагаемся что callback_data формируется нами.
        parts = data.split(":")
        # rev:action:item_id
        if len(parts) >= 3:
            await handle_callback(repo, query, {"action": parts[1], "item_id": parts[2]})
        else:
            await query.answer("Неизвестное действие")

    return dp


__all__ = ["create_router"]
