"""A tiny fake Telethon-like client for local development and tests.

This provides the minimal async API surface used by `start_telethon.py`
and collectors: connect, disconnect, is_user_authorized, get_me and start.
It should only be used in DEV mode behind a feature flag.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


class FakeClient:
    def __init__(self):
        self._connected = False

    async def connect(self):
        await asyncio.sleep(0)
        self._connected = True

    async def disconnect(self):
        await asyncio.sleep(0)
        self._connected = False

    async def is_user_authorized(self) -> bool:
        # In dev mode we treat the fake client as authorized
        return True

    async def start(self, *args, **kwargs):
        # no-op for the fake client
        return None

    async def get_me(self):
        # return a simple representation like Telethon's User
        return SimpleNamespace(id=0, username='dev_user', first_name='Dev')


__all__ = ["FakeClient"]
