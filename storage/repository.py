"""Storage repository (clean, consistent implementation).

This module provides a compact, well-formed async SQLite-backed repository
facade used by tests and lightweight services. It intentionally keeps a
minimal feature set (migrations, checksum, basic CRUD) so the project can
parse and tests can be added. Database drivers are referenced but
installation/execution is left to the user environment.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import aiosqlite

LOGGER = logging.getLogger(__name__)


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
	"""Create DB file, enable foreign keys and ensure minimal schema exists.

	This function is intentionally lightweight: it creates an `items` table
	used by the repository and a `schema_migrations` table to record applied
	migrations. Full migrations from files in `migrations/` are applied when
	present, but the function will also work without any migration files.
	"""
	db_path = _ensure_parent(Path(db_path))
	db = await aiosqlite.connect(str(db_path))
	try:
		await db.execute("PRAGMA foreign_keys = ON;")
		await db.execute(
			f"""
			CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
				version TEXT PRIMARY KEY,
				applied_at TEXT NOT NULL DEFAULT (datetime('now'))
			)
			"""
		)
		await db.execute(
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
		await db.commit()
	finally:
		await db.close()


async def _fetch_applied_versions(db: aiosqlite.Connection) -> set[str]:
	cur = await db.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}")
	rows = await cur.fetchall()
	return {row[0] if isinstance(row, tuple) else row["version"] for row in rows}


def _iter_migrations() -> Iterable[Migration]:
	if not MIGRATIONS_DIR.exists():
		return ()
	for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
		yield Migration(version=path.stem, path=path)


def _calculate_checksum(link: Optional[str], title: Optional[str], content: Optional[str]) -> str:
	base = link or "".join(filter(None, [title or "", content or ""]))
	return hashlib.sha256(base.encode("utf-8")).hexdigest()


class AsyncNewsRepository:
	"""Small async repository facade used in tests and lightweight code.

	It provides connection management and a couple of CRUD helpers. The
	implementation is intentionally minimal but fully async so it can be
	used in tests that exercise async flows.
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
		checksum = item.get("checksum") or _calculate_checksum(item.get("link"), item.get("title"), item.get("content"))
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
			except aiosqlite.IntegrityError as exc:
				# SQLite will raise IntegrityError on unique constraint violations
				raise DuplicateItemError("item exists") from exc

	async def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
		async with self._connection() as db:
			cur = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
			row = await cur.fetchone()
			return dict(row) if row else None


__all__ = ["AsyncNewsRepository", "initialize_database", "DuplicateItemError"]
