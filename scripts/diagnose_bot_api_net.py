#!/usr/bin/env python3
"""Diagnose Bot API / SOCKS reachability without printing secrets."""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


def tcp_check(host: str, port: int, timeout: float = 8.0) -> str:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL:{type(exc).__name__}:{str(exc)[:100]}"
    finally:
        s.close()


async def getme(session_factory, label: str) -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(f"{label}: no_token")
        return
    try:
        from aiogram import Bot

        bot = Bot(token=token, session=session_factory())
        me = await asyncio.wait_for(bot.get_me(), timeout=25)
        print(f"{label}: ok @{me.username} id={me.id}")
        await bot.session.close()
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: FAIL {type(exc).__name__}: {str(exc)[:200]}")


async def main() -> None:
    host = (os.getenv("PROXY_HOST") or "").strip()
    port = int(os.getenv("PROXY_PORT") or "0" or 0)
    print("cwd", os.getcwd())
    print("BOT_API_USE_PROXY", os.getenv("BOT_API_USE_PROXY"))
    print("PROXY_ENABLED", os.getenv("PROXY_ENABLED"))
    print("PROXY_HOST_set", bool(host), "PROXY_PORT", port)
    if host and port:
        print("proxy_tcp", tcp_check(host, port))
    else:
        print("proxy_tcp skipped")

    from aiogram.client.session.aiohttp import AiohttpSession

    # Direct session (no proxy)
    await getme(lambda: AiohttpSession(timeout=20), "direct")

    # Project session (with proxy chain)
    try:
        from bot_aiogram import _build_bot_session

        await getme(_build_bot_session, "project_session")
    except Exception as exc:  # noqa: BLE001
        print("project_session_build_FAIL", type(exc).__name__, str(exc)[:160])


if __name__ == "__main__":
    asyncio.run(main())
