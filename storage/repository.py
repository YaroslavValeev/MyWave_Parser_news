"""Async SQLite-backed repository used by tests and lightweight services.

This module provides a small, well-tested friendly API for storing and
retrieving news-like items. It intentionally keeps the surface area
small: database initialization, a basic migration helper, checksum
calculation and a minimal async repository class used by tests.

The implementation uses spaces-only indentation and avoids any
non-deterministic side-effects at import time.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Mapping, Optional

import aiosqlite


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_VERSION_TABLE = "schema_migrations"


class DuplicateItemError(Exception):
	"""Raised when attempting to insert an item that already exists."""


@dataclass(slots=True)
class Migration:
	version: str
	path: Path


def _ensure_parent(path: Path) -> Path:
	path = Path(path)
	if not path.parent.exists():
		path.parent.mkdir(parents=True, exist_ok=True)
	return path


async def initialize_database(db_path: Path | str) -> None:
	"""Create the database file and ensure core tables exist.

	This is safe to call multiple times.
	"""
	db_path = _ensure_parent(Path(db_path))
	conn = await aiosqlite.connect(str(db_path))
	try:
		await conn.execute("PRAGMA foreign_keys = ON;")
		# schema migrations table
		await conn.execute(
			f"""
			CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
				version TEXT PRIMARY KEY,
				applied_at TEXT NOT NULL DEFAULT (datetime('now'))
			)
			"""
		)
		# items table (simple, intentionally small set of columns)
		await conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS items (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				source TEXT,
				title TEXT,
				content TEXT,
				link TEXT UNIQUE,
				date TEXT,
				status TEXT,
				checksum TEXT,
				created_at TEXT,
				updated_at TEXT
			)
			"""
		)
		await conn.commit()
	finally:
		await conn.close()


async def _fetch_applied_versions(db: aiosqlite.Connection) -> set[str]:
	cur = await db.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}")
	rows = await cur.fetchall()
	# rows may be sequences or mapping-like depending on row_factory
	versions: set[str] = set()
	for row in rows:
		if isinstance(row, tuple):
			versions.add(row[0])
		else:
			versions.add(row["version"])
	return versions


def _iter_migrations() -> Iterable[Migration]:
	if not MIGRATIONS_DIR.exists():
		return ()
	for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
		yield Migration(version=path.stem, path=path)


def _calculate_checksum(link: Optional[str], title: Optional[str], content: Optional[str]) -> str:
	base = link or "".join(filter(None, [title or "", content or ""]))
	return hashlib.sha256(base.encode("utf-8")).hexdigest()


class AsyncNewsRepository:
	"""Minimal async repository for news items backed by SQLite.

	The class intentionally exposes a tiny API used by tests: create and
	get items. It opens and closes connections per operation which is
	simpler and safer for short-running test scenarios.
	"""

	def __init__(self, db_path: Path | str):
		self._db_path = str(db_path)

	@asynccontextmanager
	async def _connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
		conn = await aiosqlite.connect(self._db_path)
		conn.row_factory = aiosqlite.Row
		try:
			await conn.execute("PRAGMA foreign_keys = ON;")
			yield conn
		finally:
			await conn.close()

	def _prepare_item_payload(self, item: Mapping[str, Any]) -> dict[str, Any]:
		now = datetime.now(timezone.utc).isoformat()
		checksum = item.get("checksum") or _calculate_checksum(
			item.get("link"), item.get("title"), item.get("content")
		)
		return {
			"source": item.get("source") or "unknown",
			"title": item.get("title"),
			"content": item.get("content"),
			"link": item.get("link"),
			"date": item.get("date"),
			"status": item.get("status") or "new",
			"checksum": checksum,
			"created_at": item.get("created_at") or now,
			"updated_at": item.get("updated_at") or now,
		}

	async def create_item(self, item: Mapping[str, Any]) -> int:
		payload = self._prepare_item_payload(item)
		async with self._connection() as db:
			try:
				cur = await db.execute(
					"INSERT INTO items (source, title, content, link, date, status, checksum, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
					(
						payload["source"],
						payload.get("title"),
						payload.get("content"),
						payload.get("link"),
						payload.get("date"),
						payload["status"],
						payload["checksum"],
						payload["created_at"],
						payload["updated_at"],
					),
				)
				await db.commit()
				return cur.lastrowid
			except aiosqlite.IntegrityError as err:
				raise DuplicateItemError("item exists") from err

	async def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
		async with self._connection() as db:
			cur = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
			row = await cur.fetchone()
			return dict(row) if row else None


__all__ = ["AsyncNewsRepository", "initialize_database", "DuplicateItemError"]
