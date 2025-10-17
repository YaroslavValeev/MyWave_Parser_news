"""Minimal async wrapper compatible with the subset of aiosqlite we rely on."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Optional, Sequence

Row = sqlite3.Row
IntegrityError = sqlite3.IntegrityError


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self) -> Optional[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchone)

    async def fetchall(self) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchall)

    @property
    def lastrowid(self) -> int:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class Connection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, sql: str, parameters: Sequence[Any] | dict[str, Any] = ()) -> Cursor:
        cursor = await asyncio.to_thread(self._conn.execute, sql, parameters)
        return Cursor(cursor)

    async def executescript(self, script: str) -> None:
        await asyncio.to_thread(self._conn.executescript, script)

    async def commit(self) -> None:
        await asyncio.to_thread(self._conn.commit)

    async def close(self) -> None:
        await asyncio.to_thread(self._conn.close)

    async def __aenter__(self) -> "Connection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, factory):
        self._conn.row_factory = factory


async def connect(path: str | bytes) -> Connection:
    conn = await asyncio.to_thread(sqlite3.connect, path, check_same_thread=False)
    return Connection(conn)
"""Minimal async wrapper compatible with the subset of aiosqlite we rely on."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Optional, Sequence

Row = sqlite3.Row
IntegrityError = sqlite3.IntegrityError


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self) -> Optional[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchone)

    async def fetchall(self) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchall)

    @property
    def lastrowid(self) -> int:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class Connection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, sql: str, parameters: Sequence[Any] | dict[str, Any] = ()) -> Cursor:
        cursor = await asyncio.to_thread(self._conn.execute, sql, parameters)
        return Cursor(cursor)

    async def executescript(self, script: str) -> None:
        await asyncio.to_thread(self._conn.executescript, script)

    async def commit(self) -> None:
        await asyncio.to_thread(self._conn.commit)

    async def close(self) -> None:
        await asyncio.to_thread(self._conn.close)

    async def __aenter__(self) -> "Connection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, factory):
        self._conn.row_factory = factory


async def connect(path: str | bytes) -> Connection:
    conn = await asyncio.to_thread(sqlite3.connect, path, check_same_thread=False)
    return Connection(conn)
