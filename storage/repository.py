"""Async SQLite-backed repository: миграции, новости, NLP, публикации, логи."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Mapping, Optional

import aiosqlite

from utils.row_utils import generate_checksum
from utils.item_freshness import is_item_stale_for_review, review_max_age_days

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_VERSION_TABLE = "schema_migrations"


class DuplicateItemError(Exception):
    """Попытка вставить запись с уже существующим checksum."""


@dataclass(slots=True)
class Migration:
    version: str
    path: Path


def _ensure_parent(path: Path) -> Path:
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def _fetch_applied_versions(conn: aiosqlite.Connection) -> set[str]:
    cur = await conn.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}")
    rows = await cur.fetchall()
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


async def initialize_database(db_path: Path | str) -> None:
    """Создать БД и применить SQL-миграции из storage/migrations (идемпотентно)."""
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
        await conn.commit()
        conn.row_factory = aiosqlite.Row
        applied = await _fetch_applied_versions(conn)
        for mig in _iter_migrations():
            if mig.version in applied:
                continue
            sql = mig.path.read_text(encoding="utf-8")
            await conn.executescript(sql)
            await conn.execute(
                f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (?)",
                (mig.version,),
            )
            await conn.commit()
    finally:
        await conn.close()


def _calculate_checksum(link: Optional[str], title: Optional[str], content: Optional[str]) -> str:
    base = link or "".join(filter(None, [title or "", content or ""]))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def get_db_path(db_path: Path | str) -> str:
    """Абсолютный путь к файлу БД (для логов и отладки)."""
    return str(Path(db_path).resolve())


class AsyncNewsRepository:
    """Репозиторий новостей и связанных сущностей (SQLite + aiosqlite)."""

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
        title = item.get("title")
        if title is None:
            title = item.get("raw_title")
        content = item.get("content")
        if content is None:
            content = item.get("raw_content")
        link = item.get("link") or item.get("source_url") or ""
        link = str(link).strip() if link else None

        source = (item.get("source") or "").strip() or None
        if not source:
            st = (item.get("source_type") or "").strip()
            sn = (item.get("source_name") or "").strip()
            if st and sn:
                source = f"{st}:{sn}"
            elif sn:
                source = sn
            elif st:
                source = st
            else:
                source = "unknown"

        checksum = item.get("checksum") or _calculate_checksum(link, title, content)
        return {
            "source": source,
            "title": title,
            "content": content,
            "link": link,
            "date": item.get("date"),
            "status": item.get("status") or "new",
            "checksum": checksum,
            "lang": item.get("lang"),
            "created_at": item.get("created_at") or now,
            "updated_at": item.get("updated_at") or now,
            "author_user_id": item.get("author_user_id"),
            "images": item.get("images"),
            "videos": item.get("videos"),
            "transcript": item.get("transcript"),
            "comment": item.get("comment"),
        }

    async def create_item(self, item: Mapping[str, Any]) -> int:
        payload = self._prepare_item_payload(item)
        async with self._connection() as db:
            try:
                cur = await db.execute(
                    """
                    INSERT INTO items (
                        source, title, content, link, date, status, checksum, lang,
                        created_at, updated_at, author_user_id, images, videos, transcript, comment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["source"],
                        payload.get("title"),
                        payload.get("content"),
                        payload.get("link"),
                        payload.get("date"),
                        payload["status"],
                        payload["checksum"],
                        payload.get("lang"),
                        payload["created_at"],
                        payload["updated_at"],
                        payload.get("author_user_id"),
                        payload.get("images"),
                        payload.get("videos"),
                        payload.get("transcript"),
                        payload.get("comment"),
                    ),
                )
                await db.commit()
                return int(cur.lastrowid)
            except aiosqlite.IntegrityError as err:
                raise DuplicateItemError("item exists") from err

    async def item_exists_by_checksum(self, checksum: str) -> bool:
        if not (checksum or "").strip():
            return False
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT 1 FROM items WHERE checksum = ? LIMIT 1",
                (checksum.strip(),),
            )
            row = await cur.fetchone()
            return row is not None

    async def item_exists_by_content(
        self, raw_title: str, raw_content: str, raw_html: str
    ) -> bool:
        """Дубликат по той же схеме checksum, что и collector_runner (MD5 от raw_*)."""
        ch = generate_checksum(
            {
                "raw_title": raw_title or "",
                "raw_content": raw_content or "",
                "raw_html": raw_html or "",
            }
        )
        return await self.item_exists_by_checksum(ch)

    async def list_items(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Последние записи (для legacy get_latest_news)."""
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM items ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def _normalize_statuses(statuses: str | list[str] | tuple[str, ...]) -> list[str]:
        if isinstance(statuses, str):
            return [statuses]
        return list(statuses)

    async def list_items_by_status(
        self,
        statuses: str | list[str] | tuple[str, ...],
        *,
        limit: int = 100,
        order: str = "ASC",
    ) -> list[dict[str, Any]]:
        st = self._normalize_statuses(statuses)
        if not st:
            return []
        ord_sql = "DESC" if str(order).upper() == "DESC" else "ASC"
        id_ord = "DESC" if ord_sql == "DESC" else "ASC"
        placeholders = ",".join("?" for _ in st)
        q = (
            f"SELECT * FROM items WHERE status IN ({placeholders}) "
            f"ORDER BY datetime(created_at) {ord_sql}, id {id_ord} LIMIT ?"
        )
        async with self._connection() as db:
            cur = await db.execute(q, (*st, limit))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def list_review_queue(self, *, limit: int = 1) -> list[dict[str, Any]]:
        """Очередь ревью Owner: review, затем new; без материалов старше REVIEW_MAX_AGE_DAYS."""
        max_days = review_max_age_days()
        fetch_limit = max(limit * 50, limit, 50)
        q = """
        SELECT * FROM items
        WHERE status IN ('review', 'new')
        ORDER BY
            CASE status WHEN 'review' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
            datetime(created_at) ASC
        LIMIT ?
        """
        async with self._connection() as db:
            cur = await db.execute(q, (fetch_limit,))
            rows = [dict(r) for r in await cur.fetchall()]

        if max_days <= 0:
            return rows[:limit]

        fresh: list[dict[str, Any]] = []
        stale_ids: list[int] = []
        for row in rows:
            if is_item_stale_for_review(row, max_days=max_days):
                stale_ids.append(int(row["id"]))
                continue
            fresh.append(row)
            if len(fresh) >= limit:
                break

        if stale_ids:
            await self.expire_stale_review_items(stale_ids)
        return fresh[:limit]

    async def expire_stale_review_items(self, item_ids: Iterable[int]) -> int:
        """Снять устаревшие материалы с ревью (status=expired)."""
        ids = [int(i) for i in item_ids if i]
        if not ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        max_days = review_max_age_days()
        expired = 0
        async with self._connection() as db:
            for item_id in ids:
                cur = await db.execute(
                    "SELECT status FROM items WHERE id = ?",
                    (item_id,),
                )
                row = await cur.fetchone()
                if not row:
                    continue
                status = str(row["status"] or "")
                if status not in ("review", "new"):
                    continue
                await db.execute(
                    "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                    ("expired", now, item_id),
                )
                await db.execute(
                    """
                    INSERT INTO logs (item_id, level, message, meta, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        "info",
                        "review_expired_stale_publication",
                        json.dumps(
                            {"max_age_days": max_days},
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                expired += 1
            await db.commit()
        return expired

    async def upsert_author_notes(self, item_id: int, author_notes: str) -> None:
        """Сохранить комментарий/мнение владельца в nlp_results без затирания summary и т.д."""
        now = datetime.now(timezone.utc).isoformat()
        text = (author_notes or "").strip()
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO nlp_results (item_id, author_notes, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    author_notes = excluded.author_notes,
                    updated_at = excluded.updated_at,
                    version = version + 1
                """,
                (item_id, text, now),
            )
            await db.commit()

    async def update_status(self, item_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, item_id),
            )
            await db.commit()

    async def update_schedule(self, item_id: int, scheduled_at: str | None) -> None:
        """Сохранить/сбросить время отложенной публикации (UTC ISO)."""
        now = datetime.now(timezone.utc).isoformat()
        normalized = (scheduled_at or "").strip() or None
        async with self._connection() as db:
            await db.execute(
                "UPDATE items SET scheduled_at = ?, updated_at = ? WHERE id = ?",
                (normalized, now, item_id),
            )
            await db.commit()

    async def update_item_media(
        self,
        item_id: int,
        *,
        images: str | None = None,
        videos: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute(
                "UPDATE items SET images = ?, videos = ?, updated_at = ? WHERE id = ?",
                (images, videos, now, item_id),
            )
            await db.commit()

    async def update_item_content(self, item_id: int, content: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute(
                "UPDATE items SET content = ?, updated_at = ? WHERE id = ?",
                ((content or "").strip(), now, item_id),
            )
            await db.commit()

    async def requeue_error_to_new(self, *, limit: int) -> int:
        """Перевести до ``limit`` записей из status=error в new (повторный прогон NLP)."""
        lim = max(1, min(int(limit), 500))
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            cur = await db.execute(
                """
                UPDATE items SET status = 'new', updated_at = ?
                WHERE id IN (
                    SELECT id FROM items WHERE status = 'error' ORDER BY id ASC LIMIT ?
                )
                """,
                (now, lim),
            )
            await db.commit()
            return int(cur.rowcount or 0)

    async def save_nlp_results(
        self,
        item_id: int,
        *,
        summary: str | None = None,
        questions: Any = None,
        decision: str | None = None,
        moderation: Any = None,
        extra: Any = None,
        merged_text: str | None = None,
        voice_file: str | None = None,
        rewrite_guidance: str | None = None,
        **kwargs: Any,
    ) -> None:
        q_json = json.dumps(questions, ensure_ascii=False) if questions is not None else None
        extra_json = json.dumps(extra, ensure_ascii=False) if extra is not None else None
        if moderation is None:
            mod_str: str | None = None
        elif isinstance(moderation, str):
            mod_str = moderation
        else:
            mod_str = json.dumps(moderation, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO nlp_results (
                    item_id, summary, questions, decision, moderation, extra,
                    merged_text, voice_file, rewrite_guidance, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    summary = excluded.summary,
                    questions = excluded.questions,
                    decision = excluded.decision,
                    moderation = excluded.moderation,
                    extra = excluded.extra,
                    merged_text = excluded.merged_text,
                    voice_file = excluded.voice_file,
                    rewrite_guidance = excluded.rewrite_guidance,
                    updated_at = excluded.updated_at,
                    version = version + 1
                """,
                (
                    item_id,
                    summary,
                    q_json,
                    decision,
                    mod_str,
                    extra_json,
                    merged_text,
                    voice_file,
                    rewrite_guidance,
                    now,
                ),
            )
            await db.commit()

    async def get_nlp_results(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM nlp_results WHERE item_id = ?", (item_id,))
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("questions"):
                try:
                    d["questions"] = json.loads(d["questions"])
                except (TypeError, json.JSONDecodeError):
                    pass
            if d.get("extra"):
                try:
                    d["extra"] = json.loads(d["extra"])
                except (TypeError, json.JSONDecodeError):
                    pass
            if d.get("moderation"):
                try:
                    d["moderation"] = json.loads(d["moderation"])
                except (TypeError, json.JSONDecodeError):
                    pass
            return d

    async def save_publication(self, item_id: int, channel_id: str, message_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            cur = await db.execute(
                """
                INSERT INTO publications (item_id, channel_id, message_id, published_at)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, channel_id, message_id, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_publication_by_item(self, item_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM publications WHERE item_id = ? ORDER BY datetime(published_at) DESC LIMIT 1",
                (item_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def log_event(
        self,
        item_id: int | None,
        level: str,
        message: str,
        meta: Mapping[str, Any] | None = None,
    ) -> int:
        meta_json = json.dumps(dict(meta), ensure_ascii=False) if meta else None
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            cur = await db.execute(
                "INSERT INTO logs (item_id, level, message, meta, created_at) VALUES (?, ?, ?, ?, ?)",
                (item_id, level, message, meta_json, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_last_log(self, item_id: int, message: str) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM logs
                WHERE item_id = ? AND message = ?
                ORDER BY datetime(created_at) DESC LIMIT 1
                """,
                (item_id, message),
            )
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("meta"):
                try:
                    d["meta"] = json.loads(d["meta"])
                except (TypeError, json.JSONDecodeError):
                    d["meta"] = {}
            return d

    async def fetch_recent_item_logs(
        self,
        item_id: int,
        *,
        limit: int = 5,
        owner_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Последние события по материалу (для audit snippet в Admin preview)."""
        limit = max(1, min(int(limit), 50))
        async with self._connection() as db:
            if owner_only:
                cur = await db.execute(
                    """
                    SELECT * FROM logs
                    WHERE item_id = ? AND message LIKE 'owner_%'
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT ?
                    """,
                    (item_id, limit),
                )
            else:
                cur = await db.execute(
                    """
                    SELECT * FROM logs
                    WHERE item_id = ?
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT ?
                    """,
                    (item_id, limit),
                )
            rows = await cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            if d.get("meta"):
                try:
                    d["meta"] = json.loads(d["meta"])
                except (TypeError, json.JSONDecodeError):
                    d["meta"] = {}
            result.append(d)
        return result

    async def upsert_contacts(self, contacts: Iterable[Mapping[str, Any]]) -> int:
        """Вставить или обновить контакты по уникальному contact_id. Возвращает число обработанных строк."""
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        async with self._connection() as db:
            for c in contacts:
                cid = (c.get("contact_id") or "").strip()
                if not cid:
                    continue
                await db.execute(
                    """
                    INSERT INTO contacts (
                        contact_id, source, type, value, date_found, item_link, item_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contact_id) DO UPDATE SET
                        source = excluded.source,
                        type = excluded.type,
                        value = excluded.value,
                        date_found = excluded.date_found,
                        item_link = excluded.item_link,
                        item_id = excluded.item_id
                    """,
                    (
                        cid,
                        c.get("source") or "unknown",
                        c.get("type") or "unknown",
                        str(c.get("value") or "").strip(),
                        c.get("date_found"),
                        c.get("item_link"),
                        c.get("item_id"),
                        now,
                    ),
                )
                n += 1
            await db.commit()
        return n

    async def delete_items_before(self, cutoff: datetime) -> int:
        cutoff_s = cutoff.isoformat()
        async with self._connection() as db:
            cur = await db.execute(
                "DELETE FROM items WHERE datetime(created_at) < datetime(?)",
                (cutoff_s,),
            )
            await db.commit()
            return int(cur.rowcount)

    async def list_publication_candidates(self, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM items
                WHERE status IN ('approved', 'ready_to_publish', 'publish_retry')
                ORDER BY
                    CASE status
                        WHEN 'ready_to_publish' THEN 0
                        WHEN 'approved' THEN 1
                        WHEN 'publish_retry' THEN 2
                        ELSE 4
                    END ASC,
                    CASE WHEN status = 'publish_retry' THEN datetime(updated_at) END DESC,
                    CASE WHEN status != 'publish_retry' THEN datetime(updated_at) END ASC,
                    id ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_status_counts(self) -> dict[str, int]:
        async with self._connection() as db:
            cur = await db.execute("SELECT status, COUNT(*) AS c FROM items GROUP BY status")
            rows = await cur.fetchall()
            out: dict[str, int] = {}
            for row in rows:
                out[str(row["status"] or "")] = int(row["c"])
            return out

    async def get_processing_summary(self) -> dict[str, Any]:
        async with self._connection() as db:
            cur = await db.execute("SELECT COUNT(*) FROM items WHERE status = 'new'")
            row = await cur.fetchone()
            pending = int(row[0]) if row else 0
            cur2 = await db.execute("SELECT COUNT(*) FROM items WHERE status = 'processing'")
            row2 = await cur2.fetchone()
            processing = int(row2[0]) if row2 else 0
        return {"nlp_pending": pending, "nlp_processing": processing}

    async def count_publication_queue(self) -> int:
        """Записи, ожидающие отправку в целевой чат (/publish)."""
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT COUNT(*) FROM items
                WHERE status IN ('approved', 'ready_to_publish', 'publish_retry')
                """
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_audit_export_cursor(self, key: str) -> int:
        """Последний выгруженный id из logs (0 если ещё не выгружали)."""
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT value FROM audit_export_state WHERE key = ?",
                (key,),
            )
            row = await cur.fetchone()
            if not row:
                return 0
            try:
                return int(row[0] if isinstance(row, tuple) else row["value"])
            except (TypeError, ValueError):
                return 0

    async def set_audit_export_cursor(self, key: str, last_log_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO audit_export_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(last_log_id), now),
            )
            await db.commit()

    async def fetch_owner_action_logs_after(
        self,
        last_log_id: int,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Строки logs с действиями Owner (префикс owner_), id > last_log_id."""
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT id, item_id, level, message, meta, created_at
                FROM logs
                WHERE id > ? AND message LIKE 'owner_%'
                ORDER BY id ASC
                LIMIT ?
                """,
                (last_log_id, limit),
            )
            rows = await cur.fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                if d.get("meta"):
                    try:
                        d["meta"] = json.loads(d["meta"])
                    except (TypeError, json.JSONDecodeError):
                        d["meta"] = {}
                else:
                    d["meta"] = {}
                out.append(d)
            return out

    async def upsert_channel_commenters(
        self, records: Iterable[Mapping[str, Any]]
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        async with self._connection() as db:
            for rec in records:
                cid = str(rec.get("commenter_id") or "").strip()
                if not cid:
                    continue
                await db.execute(
                    """
                    INSERT INTO channel_commenters (
                        commenter_id, channel_url, channel_title, post_id, message_id,
                        user_id, user_name, comment_text, comment_at, source_name,
                        synced_to_sheet, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(commenter_id) DO UPDATE SET
                        channel_title = excluded.channel_title,
                        user_id = excluded.user_id,
                        user_name = excluded.user_name,
                        comment_text = excluded.comment_text,
                        comment_at = excluded.comment_at,
                        source_name = excluded.source_name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cid,
                        rec.get("channel_url") or "",
                        rec.get("channel_title"),
                        rec.get("post_id") or "",
                        rec.get("message_id") or "",
                        rec.get("user_id"),
                        rec.get("user_name"),
                        rec.get("comment_text"),
                        rec.get("comment_at"),
                        rec.get("source_name"),
                        now,
                        now,
                    ),
                )
                n += 1
            await db.commit()
        return n

    async def count_channel_commenters(self) -> int:
        async with self._connection() as db:
            cur = await db.execute("SELECT COUNT(*) FROM channel_commenters")
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def list_channel_commenters_unsynced(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM channel_commenters
                WHERE synced_to_sheet = 0
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def mark_channel_commenters_synced(self, commenter_ids: Iterable[str]) -> int:
        ids = [str(x).strip() for x in commenter_ids if str(x).strip()]
        if not ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        async with self._connection() as db:
            for cid in ids:
                await db.execute(
                    """
                    UPDATE channel_commenters
                    SET synced_to_sheet = 1, updated_at = ?
                    WHERE commenter_id = ?
                    """,
                    (now, cid),
                )
            await db.commit()
        return len(ids)

    async def upsert_source_health(self, tick: Mapping[str, Any]) -> None:
        """Записать метрики одного тика сбора по источнику (Stage 1 telemetry)."""
        key = str(tick.get("source_key") or "").strip()
        if not key:
            return
        now = datetime.now(timezone.utc).isoformat()
        ok = bool(tick.get("ok"))
        collected = int(tick.get("collected") or 0)
        parsed = int(tick.get("parsed") or 0)
        duplicates = int(tick.get("duplicates") or 0)
        rejected = int(tick.get("rejected") or 0)
        errors = int(tick.get("errors") or 0)
        latency = float(tick.get("latency_ms") or 0.0)
        err_text = str(tick.get("error") or "")[:500]
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT consecutive_failures FROM source_health WHERE source_key = ?",
                (key,),
            )
            prev = await cur.fetchone()
            prev_streak = int(prev["consecutive_failures"]) if prev else 0
            streak = 0 if ok else prev_streak + 1
            await db.execute(
                """
                INSERT INTO source_health (
                    source_key, source_type, source_name, source_url,
                    last_success_at, last_failure_at, last_error, last_latency_ms,
                    consecutive_failures,
                    collected_total, parsed_total, duplicates_total, rejected_total, errors_total,
                    last_collected, last_parsed, last_duplicates, last_rejected, last_errors,
                    last_ok, updated_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?
                )
                ON CONFLICT(source_key) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_name = excluded.source_name,
                    source_url = excluded.source_url,
                    last_success_at = CASE
                        WHEN excluded.last_ok = 1 THEN excluded.last_success_at
                        ELSE source_health.last_success_at
                    END,
                    last_failure_at = CASE
                        WHEN excluded.last_ok = 0 THEN excluded.last_failure_at
                        ELSE source_health.last_failure_at
                    END,
                    last_error = excluded.last_error,
                    last_latency_ms = excluded.last_latency_ms,
                    consecutive_failures = excluded.consecutive_failures,
                    collected_total = source_health.collected_total + excluded.last_collected,
                    parsed_total = source_health.parsed_total + excluded.last_parsed,
                    duplicates_total = source_health.duplicates_total + excluded.last_duplicates,
                    rejected_total = source_health.rejected_total + excluded.last_rejected,
                    errors_total = source_health.errors_total + excluded.last_errors,
                    last_collected = excluded.last_collected,
                    last_parsed = excluded.last_parsed,
                    last_duplicates = excluded.last_duplicates,
                    last_rejected = excluded.last_rejected,
                    last_errors = excluded.last_errors,
                    last_ok = excluded.last_ok,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    str(tick.get("source_type") or "unknown"),
                    str(tick.get("source_name") or "")[:200],
                    str(tick.get("source_url") or "")[:500],
                    now if ok else None,
                    None if ok else now,
                    "" if ok else err_text,
                    latency,
                    streak,
                    collected,
                    parsed,
                    duplicates,
                    rejected,
                    errors,
                    collected,
                    parsed,
                    duplicates,
                    rejected,
                    errors,
                    1 if ok else 0,
                    now,
                ),
            )
            await db.commit()

    async def list_source_health(self, *, limit: int = 500) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM source_health
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_source_health(self, source_key: str) -> dict[str, Any] | None:
        key = (source_key or "").strip()
        if not key:
            return None
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM source_health WHERE source_key = ?",
                (key,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None


__all__ = [
    "AsyncNewsRepository",
    "DuplicateItemError",
    "get_db_path",
    "initialize_database",
]
