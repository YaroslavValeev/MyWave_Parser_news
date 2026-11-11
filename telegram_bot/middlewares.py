from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config.settings import config
from storage.repository import AsyncNewsRepository

LOGGER = logging.getLogger(__name__)


class RepositoryMiddleware(BaseMiddleware):
    """Прокидывает репозиторий в data для хендлеров и фильтров."""

    def __init__(self, repository: AsyncNewsRepository) -> None:
        self._repository = repository

    async def __call__(self, handler, event: TelegramObject, data: dict):
        data["repository"] = self._repository
        return await handler(event, data)


class AccessLogMiddleware(BaseMiddleware):
    """Минимальный лог входящих апдейтов (без PII в meta)."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        LOGGER.debug("telegram_update type=%s", type(event).__name__)
        return await handler(event, data)


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


__all__ = [
    "AccessLogMiddleware",
    "RepositoryMiddleware",
    "RoleMiddleware",
]
