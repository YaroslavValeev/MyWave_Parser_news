"""Telethon session manager: string-session first, no destroy on proxy blips."""
from __future__ import annotations

import asyncio
import logging
import os

from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, SessionPasswordNeededError
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

# Один клиент на процесс: иначе database is locked + .session.bak.
_process_client: TelegramClient | None = None
_process_lock = asyncio.Lock()


def _telethon_proxy_from_env():
    """Собрать tuple proxy для Telethon из PROXY_* (или None)."""
    enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    host = (os.getenv("PROXY_HOST") or "").strip()
    if not enabled or not host:
        return None
    ptype = (os.getenv("PROXY_TYPE") or "socks5").strip().lower()
    port = int(os.getenv("PROXY_PORT", "1080"))
    user = os.getenv("PROXY_USER")
    pwd = os.getenv("PROXY_PASS")
    if user and pwd:
        return (ptype, host, port, True, user, pwd)
    return (ptype, host, port)


def _load_string_session() -> str | None:
    raw = (os.getenv("TELETHON_STRING_SESSION") or "").strip()
    if raw:
        return raw
    path = os.getenv("TELETHON_STRING_SESSION_FILE", "session_string.txt")
    if not os.path.exists(path):
        return None
    try:
        text = open(path, "r", encoding="utf-8").read().strip()
        return text or None
    except OSError:
        return None


class TelegramSessionManager:
    def __init__(self, api_id, api_hash, phone):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_file = os.getenv("TELETHON_SESSION_FILE", "session_name.session")
        self.string_session = _load_string_session()
        self.string_session_file = os.getenv(
            "TELETHON_STRING_SESSION_FILE", "session_string.txt"
        )
        self.proxy = _telethon_proxy_from_env()
        self.client = None

    def _client_kwargs(self) -> dict:
        kwargs: dict = {
            "connection_retries": 5,
            "retry_delay": 2,
            "timeout": 30,
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def get_client(self):
        """Вернуть живой клиент; процесс-глобальный singleton."""
        global _process_client
        async with _process_lock:
            if self.client and self.client.is_connected():
                return self.client
            if _process_client is not None and _process_client.is_connected():
                self.client = _process_client
                return self.client
            # Старый клиент мог отвалиться — пересоздаём.
            _process_client = None
            self.client = None
            for attempt in range(1, 4):
                client = await self._create_client()
                if client is not None:
                    self.client = client
                    _process_client = client
                    return client
                logger.warning("Telethon get_client attempt=%s failed", attempt)
                await asyncio.sleep(2 * attempt)
            return None

    async def _connect_string_session(self) -> TelegramClient | None:
        if not self.string_session:
            return None
        client = TelegramClient(
            StringSession(self.string_session),
            self.api_id,
            self.api_hash,
            **self._client_kwargs(),
        )
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001
            # Proxy/timeout — сессию НЕ инвалидируем.
            logger.error("StringSession connect failed (proxy/network): %s", type(exc).__name__)
            try:
                await client.disconnect()
            except Exception:
                pass
            return None
        try:
            if not await client.is_user_authorized():
                logger.error("StringSession подключена, но не авторизована")
                await client.disconnect()
                return None
            me = await client.get_me()
            logger.info(
                "Telethon StringSession OK as=%s",
                getattr(me, "username", None) or getattr(me, "id", "?"),
            )
            return client
        except Exception as exc:  # noqa: BLE001
            logger.error("StringSession auth check failed: %s", type(exc).__name__)
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def _create_client(self):
        """Создать клиент. Приоритет: string session (без sqlite lock)."""
        client = await self._connect_string_session()
        if client is not None:
            return client

        # Fallback: file session — только если нет string session.
        if self.string_session:
            # String есть, но connect не удался (прокси). Не трогаем .session файл.
            logger.error(
                "StringSession не поднялась (сеть/прокси). "
                "Файл session_name.session НЕ трогаем."
            )
            return None

        if not os.path.exists(self.session_file):
            logger.error("Нет session_string.txt и нет %s — нужен telethon_login_once.py", self.session_file)
            return None

        client = TelegramClient(
            self.session_file,
            self.api_id,
            self.api_hash,
            **self._client_kwargs(),
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                # Не бэкапим при сетевых сбоях — только явная неавторизованность после connect.
                logger.error("File session connect OK, but not authorized")
                await client.disconnect()
                return None
            logger.info("Telethon file session OK as=%s", await client.get_me())
            # Сохранить string session для следующих запусков.
            try:
                ss = StringSession.save(client.session)
                tmp = f"{self.string_session_file}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(ss)
                os.replace(tmp, self.string_session_file)
                self.string_session = ss
                logger.info("String session saved to %s", self.string_session_file)
            except Exception as write_err:  # noqa: BLE001
                logger.warning("Failed to persist string session: %s", write_err)
            return client
        except AuthKeyUnregisteredError:
            logger.error("AuthKeyUnregistered — сессия отозвана, нужен повторный login")
            try:
                await client.disconnect()
            except Exception:
                pass
            return None
        except Exception as exc:  # noqa: BLE001
            # ProxyError / timeout / locked — НЕ удалять и НЕ переименовывать .session
            logger.error("File session connect failed: %s", type(exc).__name__)
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def close_client(self):
        """Отключить клиент этого менеджера; процесс-singleton тоже сбрасываем."""
        global _process_client
        async with _process_lock:
            client = self.client or _process_client
            self.client = None
            _process_client = None
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["TelegramSessionManager", "_telethon_proxy_from_env"]
