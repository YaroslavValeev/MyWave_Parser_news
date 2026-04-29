"""Async SQLite-backed repository used by tests and lightweight services."""
from __future__ import annotations

import hashlib
import json
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
    """Create the SQLite schema used by local services and tests."""

    db_path = _ensure_parent(Path(db_path))
    conn = await aiosqlite.connect(str(db_path))
    try:
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'unknown',
                title TEXT,
                content TEXT,
                link TEXT UNIQUE,
                date TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                checksum TEXT UNIQUE,
                images TEXT,
                videos TEXT,
                transcript TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nlp_results (
                item_id INTEGER PRIMARY KEY,
                summary TEXT,
                questions TEXT,
                decision TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                published_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                meta TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'unknown',
                type TEXT NOT NULL DEFAULT 'unknown',
                value TEXT NOT NULL,
                date_found TEXT,
                item_link TEXT,
                item_id INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.commit()
    finally:
        await conn.close()


async def _fetch_applied_versions(db: aiosqlite.Connection) -> set[str]:
    cur = await db.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}")
    rows = await cur.fetchall()
    versions: set[str] = set()
    for row in rows:
        versions.add(row[0] if isinstance(row, tuple) else row["version"])
    return versions


def _iter_migrations() -> Iterable[Migration]:
    if not MIGRATIONS_DIR.exists():
        return ()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        yield Migration(version=path.stem, path=path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calculate_checksum(link: Optional[str], title: Optional[str], content: Optional[str]) -> str:
    base = link or "|".join([title or "", content or ""])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class AsyncNewsRepository:
    """Small async repository for news items backed by SQLite."""

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
        now = _utc_now()
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
            "images": item.get("images"),
            "videos": item.get("videos"),
            "transcript": item.get("transcript"),
            "comment": item.get("comment"),
            "created_at": item.get("created_at") or now,
            "updated_at": item.get("updated_at") or now,
        }

    async def create_item(self, item: Mapping[str, Any]) -> int:
        payload = self._prepare_item_payload(item)
        async with self._connection() as db:
            try:
                cur = await db.execute(
                    """
                    INSERT INTO items (
                        source, title, content, link, date, status, checksum,
                        images, videos, transcript, comment, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["source"],
                        payload.get("title"),
                        payload.get("content"),
                        payload.get("link"),
                        payload.get("date"),
                        payload["status"],
                        payload["checksum"],
                        payload.get("images"),
                        payload.get("videos"),
                        payload.get("transcript"),
                        payload.get("comment"),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                await db.commit()
                return int(cur.lastrowid)
            except aiosqlite.IntegrityError as err:
                raise DuplicateItemError("item exists") from err

    async def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_items(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM items ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def list_publication_candidates(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM items
                WHERE status IN ('new', 'review', 'ready')
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def update_status(self, item_id: int, status: str) -> None:
        async with self._connection() as db:
            await db.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, _utc_now(), item_id),
            )
            await db.commit()

    async def save_nlp_results(
        self, item_id: int, *, summary: str, questions: list[str], decision: str
    ) -> None:
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO nlp_results (item_id, summary, questions, decision, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    summary = excluded.summary,
                    questions = excluded.questions,
                    decision = excluded.decision,
                    updated_at = excluded.updated_at
                """,
                (item_id, summary, json.dumps(questions, ensure_ascii=False), decision, _utc_now()),
            )
            await db.commit()

    async def get_nlp_results(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM nlp_results WHERE item_id = ?", (item_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["questions"] = json.loads(result.get("questions") or "[]")
            return result

    async def save_publication(self, item_id: int, channel_id: str, message_id: str) -> int:
        async with self._connection() as db:
            cur = await db.execute(
                """
                INSERT INTO publications (item_id, channel_id, message_id, published_at)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, channel_id, message_id, _utc_now()),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_publication_by_item(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM publications WHERE item_id = ? ORDER BY id DESC LIMIT 1",
                (item_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def log_event(
        self, item_id: int | None, level: str, message: str, meta: Mapping[str, Any] | None = None
    ) -> int:
        async with self._connection() as db:
            cur = await db.execute(
                """
                INSERT INTO event_logs (item_id, level, message, meta, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, level, message, json.dumps(meta or {}, ensure_ascii=False), _utc_now()),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_last_log(self, item_id: int, message: str | None = None) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            if message is None:
                cur = await db.execute(
                    "SELECT * FROM event_logs WHERE item_id = ? ORDER BY id DESC LIMIT 1",
                    (item_id,),
                )
            else:
                cur = await db.execute(
                    """
                    SELECT * FROM event_logs
                    WHERE item_id = ? AND message = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (item_id, message),
                )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_items_before(self, cutoff: datetime) -> int:
        cutoff_text = cutoff.isoformat()
        async with self._connection() as db:
            cur = await db.execute("DELETE FROM items WHERE created_at < ?", (cutoff_text,))
            await db.commit()
            return int(cur.rowcount or 0)

    async def upsert_contacts(self, contacts: Iterable[Mapping[str, Any]]) -> int:
        affected = 0
        async with self._connection() as db:
            for contact in contacts:
                await db.execute(
                    """
                    INSERT INTO contacts (
                        contact_id, source, type, value, date_found, item_link, item_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contact_id) DO UPDATE SET
                        source = excluded.source,
                        type = excluded.type,
                        value = excluded.value,
                        date_found = excluded.date_found,
                        item_link = excluded.item_link,
                        item_id = excluded.item_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        contact.get("contact_id"),
                        contact.get("source") or "unknown",
                        contact.get("type") or "unknown",
                        contact.get("value"),
                        contact.get("date_found"),
                        contact.get("item_link"),
                        contact.get("item_id"),
                        _utc_now(),
                    ),
                )
                affected += 1
            await db.commit()
        return affected


__all__ = ["AsyncNewsRepository", "initialize_database", "DuplicateItemError"]
