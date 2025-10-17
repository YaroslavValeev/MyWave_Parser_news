from __future__ import annotations

from aiogram import types
from aiogram.types import Message, CallbackQuery

from .keyboards import review_keyboard
from storage.repository import AsyncNewsRepository, initialize_database, get_db_path


async def show_review_item(repo: AsyncNewsRepository, item_id: int, chat: types.Chat):
    item = await repo.get_item(item_id)
    text = f"<b>{item['title']}</b>\n{item['summary'] or ''}\n\nИсточник: {item.get('source_url', '')}"
    await chat.send_message(text, parse_mode="HTML", reply_markup=review_keyboard(item_id, include_publish=True))


async def handle_callback(repo: AsyncNewsRepository, query: CallbackQuery, callback_data: dict):
    action = callback_data.get("action")
    item_id = int(callback_data.get("item_id"))
    if action == "approve":
        await repo.update_status(item_id, "approved")
        await query.message.edit_text("Одобрено")
    elif action == "discard":
        await repo.update_status(item_id, "discarded")
        await query.message.edit_text("Отклонено")
    elif action == "publish_now":
        await repo.update_status(item_id, "ready_to_publish")
        await query.message.edit_text("Отправлено в публикацию")
    else:
        await query.answer("Действие не поддерживается")


__all__ = ["show_review_item", "handle_callback"]
