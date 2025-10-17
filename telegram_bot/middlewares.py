from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from config.settings import config


class RoleMiddleware(BaseMiddleware):
    """Добавляет роль пользователя в контекст (простая реализация).

    Эта middleware читает chat_id из сообщения и помечает роль "editor" если
    это один из редакторов, иначе "user".
    """

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        else:
            user_id = None

        # Очень простой подход — в будущем можно смотреть в базу пользователей
        editors = {int(x) for x in (config.EDITORS_CHAT_ID or "").split(',') if x}
        data.setdefault("role", "user")
        if user_id and user_id in editors:
            data["role"] = "editor"
        return await handler(event, data)


__all__ = ["RoleMiddleware"]
