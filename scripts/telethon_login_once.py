#!/usr/bin/env python3
"""Одноразовая интерактивная авторизация Telethon.

Запускать как файл (не через python <<'PY'), иначе input() получит EOF.
Сохраняет session_name.session и session_string.txt.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

from config.settings import config  # noqa: E402


def _proxy():
    if not config.PROXY_ENABLED or not config.PROXY_HOST:
        return None
    if config.PROXY_USER and config.PROXY_PASS:
        return (
            config.PROXY_TYPE,
            config.PROXY_HOST,
            int(config.PROXY_PORT),
            True,
            config.PROXY_USER,
            config.PROXY_PASS,
        )
    return (config.PROXY_TYPE, config.PROXY_HOST, int(config.PROXY_PORT))


async def main() -> None:
    session_file = os.getenv("TELETHON_SESSION_FILE", "session_name.session")
    string_file = os.getenv("TELETHON_STRING_SESSION_FILE", "session_string.txt")
    proxy = _proxy()
    print(
        "proxy=",
        f"{config.PROXY_HOST}:{config.PROXY_PORT}" if proxy else "off",
        flush=True,
    )

    client = TelegramClient(
        session_file,
        config.TELEGRAM_API_ID_USER,
        config.TELEGRAM_API_HASH_USER,
        proxy=proxy,
        connection_retries=5,
    )
    await client.connect()
    if not await client.is_user_authorized():
        phone = config.TELEGRAM_PHONE or ""
        print(f"need_login phone={phone[:4]}***", flush=True)
        print("Ждите код в Telegram / SMS и введите его ниже.", flush=True)
        await client.start(phone=config.TELEGRAM_PHONE)
    me = await client.get_me()
    print("authorized_as=", getattr(me, "username", None) or me.id, flush=True)

    ss = StringSession.save(client.session)
    Path(string_file).write_text(ss, encoding="utf-8")
    print("saved_file_session=", session_file, "exists=", Path(session_file).exists(), flush=True)
    print("saved_string_session=", string_file, "len=", len(ss), flush=True)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
