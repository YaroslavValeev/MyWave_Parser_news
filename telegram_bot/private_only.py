"""Ограничение команд бота: только личные чаты (не канал/группа публикации)."""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from telegram_bot.keyboards import (
    MENU_HELP,
    MENU_PARSE,
    MENU_PROBE,
    MENU_PUBLISH,
    MENU_REVIEW,
    MENU_STATS,
)

LOGGER = logging.getLogger(__name__)

_MENU_TEXTS = {
    MENU_HELP,
    MENU_PARSE,
    MENU_PROBE,
    MENU_PUBLISH,
    MENU_REVIEW,
    MENU_STATS,
}


def is_private_chat(message: Message | None) -> bool:
    if message is None or message.chat is None:
        return False
    return message.chat.type == ChatType.PRIVATE


def _is_editorial_input(message: Message) -> bool:
    text = (message.text or message.caption or "").strip()
    if not text:
        return False
    if text.startswith("/"):
        return True
    return text in _MENU_TEXTS


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Игнорировать команды и кнопки меню в группах/каналах.

    Публикация в CHANNEL_ID идёт через Bot API send_message, без команд.
    Редакторские команды (/parse, /review, …) — только в личке с ботом.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Message) and _is_editorial_input(event) and not is_private_chat(event):
            LOGGER.info(
                "ignore_non_private_editorial chat_type=%s",
                getattr(event.chat, "type", None),
            )
            return None
        return await handler(event, data)


__all__ = ["PrivateChatOnlyMiddleware", "is_private_chat"]
