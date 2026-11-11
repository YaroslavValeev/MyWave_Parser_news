"""РўРѕС‡РєР° РІС…РѕРґР° РґР»СЏ СЂРµРґР°РєС‚РѕСЂСЃРєРѕРіРѕ Telegram-Р±РѕС‚Р° РЅР° aiogram."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from config.settings import config
from storage.data import get_repository
from storage.repository import AsyncNewsRepository
from telegram_bot import create_router
from telegram_bot.keyboards import review_keyboard
from telegram_bot.middlewares import AccessLogMiddleware, RepositoryMiddleware
from telegram_bot.private_only import PrivateChatOnlyMiddleware
from telegram_bot.views import format_item_card
from services.scheduler import SchedulerService

LOGGER = logging.getLogger(__name__)


def _build_bot_session() -> AiohttpSession:
    """HTTP-сессия Bot API: EU SOCKS при BOT_API_USE_PROXY / BOT_API_PROXY_URL.

    Без прокси с RU часто бывает TelegramNetworkError Request timeout (~60 с)
    ещё до старта /parse (на первом message.answer).
    """
    timeout = float(getattr(config, "BOT_API_REQUEST_TIMEOUT", 120) or 120)
    dedicated = config.bot_api_dedicated_proxy_url()
    primary = dedicated or config.bot_api_proxy_url()
    chain: list[str] = []
    if primary:
        chain.append(primary)
    for url in config.bot_api_proxy_fallback_urls():
        if url and url not in chain:
            chain.append(url)

    if len(chain) > 1:
        LOGGER.info(
            "Bot API session: proxy chain len=%s primary=%s timeout=%ss",
            len(chain),
            config.bot_proxy_endpoint_hint(),
            int(timeout),
        )
        return AiohttpSession(proxy=chain, timeout=timeout)
    if len(chain) == 1:
        LOGGER.info(
            "Bot API session: proxy=%s timeout=%ss",
            config.bot_proxy_endpoint_hint(),
            int(timeout),
        )
        return AiohttpSession(proxy=chain[0], timeout=timeout)

    LOGGER.warning(
        "Bot API session: прямой api.telegram.org (прокси выкл). "
        "На RU риск timeout — задайте BOT_API_USE_PROXY=true и PROXY_* "
        "или BOT_API_PROXY_URL=socks5://..."
    )
    return AiohttpSession(timeout=timeout)


_EDITOR_BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="parse", description="Собрать все источники"),
    BotCommand(command="review", description="Очередь ревью"),
    BotCommand(command="deferred", description="Отложенные материалы"),
    BotCommand(command="publish", description="Опубликовать одобренные"),
    BotCommand(command="stats", description="Краткий статус"),
    BotCommand(command="probe", description="Проверить один источник"),
    BotCommand(command="item", description="Открыть материал по id"),
    BotCommand(command="report", description="Отчёт по сбору"),
    BotCommand(command="cancel", description="Отменить сценарий"),
]


async def _configure_bot_commands(bot: Bot) -> None:
    """Команды только в личке; во всех групповых scope меню очищаем.

    Старый Default-scope (с /media_diag, /requeue_nlp…) иначе остаётся в WakeSurfNews.
    Middleware PrivateChatOnly всё равно игнорирует команды в группах.
    """
    for attempt in range(1, 4):
        try:
            # Снести все старые меню (Default + группы + админы групп).
            await bot.delete_my_commands(scope=BotCommandScopeDefault(), request_timeout=90)
            await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats(), request_timeout=90)
            await bot.delete_my_commands(
                scope=BotCommandScopeAllChatAdministrators(),
                request_timeout=90,
            )
            channel = (getattr(config, "CHANNEL_ID", None) or "").strip()
            if channel:
                try:
                    chat_id: int | str = int(channel)
                except ValueError:
                    chat_id = channel
                await bot.delete_my_commands(
                    scope=BotCommandScopeChat(chat_id=chat_id),
                    request_timeout=90,
                )
            # Только личные чаты — редакционные команды.
            await bot.set_my_commands(
                _EDITOR_BOT_COMMANDS,
                scope=BotCommandScopeAllPrivateChats(),
                request_timeout=90,
            )
            LOGGER.info("bot_commands: private set, default+groups cleared (attempt=%s)", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "bot_commands configure failed attempt=%s/%s: %s",
                attempt,
                3,
                type(exc).__name__,
            )
            await asyncio.sleep(5 * attempt)
    LOGGER.warning(
        "bot_commands: не удалось обновить меню (таймаут API). "
        "Команды в группах всё равно игнорируются middleware."
    )


async def review_notifier(bot: Bot, repository: AsyncNewsRepository) -> None:
    """Р Р°СЃСЃС‹Р»Р°РµС‚ РЅРѕРІС‹Рµ РєР°СЂС‚РѕС‡РєРё РІ С‡Р°С‚ СЂРµРґР°РєС‚РѕСЂРѕРІ."""

    if not config.EDITORS_CHAT_ID:
        LOGGER.warning("EDITORS_CHAT_ID РЅРµ Р·Р°РґР°РЅ, СѓРІРµРґРѕРјР»РµРЅРёСЏ РѕС‚РєР»СЋС‡РµРЅС‹")
        return

    chat_id: Optional[int | str]
    try:
        chat_id = int(config.EDITORS_CHAT_ID)
    except (TypeError, ValueError):
        chat_id = config.EDITORS_CHAT_ID

    seen: set[int] = set()
    while True:
        items = await repository.list_items_by_status(["review"], limit=20, order="DESC")
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
        raise RuntimeError("TELEGRAM_BOT_TOKEN РЅРµ Р·Р°РґР°РЅ РІ РѕРєСЂСѓР¶РµРЅРёРё")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repository = await get_repository()
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        session=_build_bot_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(RepositoryMiddleware(repository))
    dispatcher.message.outer_middleware(PrivateChatOnlyMiddleware())
    dispatcher.message.outer_middleware(AccessLogMiddleware())
    dispatcher.callback_query.outer_middleware(AccessLogMiddleware())
    dispatcher.include_router(create_router(repository, bot))

    scheduler_service = SchedulerService(repository, bot)
    await scheduler_service.start()
    notifier_task = asyncio.create_task(review_notifier(bot, repository))
    # После старта polling — иначе setMyCommands может зависнуть на минуту и шуметь ERROR.
    commands_task = asyncio.create_task(_configure_bot_commands(bot))

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        commands_task.cancel()
        notifier_task.cancel()
        with suppress(asyncio.CancelledError):
            await commands_task
        with suppress(asyncio.CancelledError):
            await notifier_task
        await scheduler_service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
