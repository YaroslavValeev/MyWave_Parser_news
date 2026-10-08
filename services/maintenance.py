"""Ежедневное обслуживание: очистка старых медиа и резервная копия SQLite."""

from __future__ import annotations

import logging
import json
from contextlib import closing
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import config
from utils.media_utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

LOGGER = logging.getLogger(__name__)

_MEDIA_SUFFIXES = {ext.lower() for ext in (*IMAGE_EXTENSIONS, *VIDEO_EXTENSIONS)}


def cleanup_downloads(
    root: Path | str = "downloads", *, days: int, protected=()
) -> tuple[int, int]:
    """Удалить медиафайлы старше ``days`` дней. Возвращает (файлов, байт).

    Файлы, используемые карточками, передаются в protected и сохраняются.
    Символические ссылки и пути за пределами root не удаляются.
    """
    base = Path(root)
    if days <= 0 or not base.is_dir():
        return 0, 0
    if base.is_symlink():
        return 0, 0
    resolved = base.resolve()
    protected = {Path(path).resolve() for path in protected}
    cutoff = time.time() - days * 86400
    removed = freed = 0
    for path in base.rglob("*"):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() not in _MEDIA_SUFFIXES
        ):
            continue
        try:
            target = path.resolve()
            if not target.is_relative_to(resolved) or target in protected:
                continue
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
            freed += stat.st_size
        except OSError as exc:
            LOGGER.warning(
                "downloads cleanup skip path=%s error=%s", path, type(exc).__name__
            )
    return removed, freed


def referenced_downloads(db_path, root="downloads") -> set[Path]:
    """Read the reference inventory; failure must prevent cleanup."""
    base = Path(root).resolve()
    found = set()

    def add(value):
        if isinstance(value, dict):
            for child in value.values():
                add(child)
        elif isinstance(value, list):
            for child in value:
                add(child)
        elif isinstance(value, str):
            for text in value.splitlines():
                text = text.strip().replace("\\", "/")
                if text.startswith(("http://", "https://")):
                    continue
                text = text.removeprefix("/static/").removeprefix("static/")
                path = Path(text)
                if path.suffix.lower() in _MEDIA_SUFFIXES:
                    path = path.resolve()
                    if path.is_relative_to(base):
                        found.add(path)

    with closing(
        sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        for image, video in db.execute("SELECT images,videos FROM items"):
            for value in (image, video):
                if value:
                    try:
                        add(json.loads(value))
                    except ValueError:
                        add(value)
        for (value,) in db.execute("SELECT extra FROM nlp_results"):
            if value:
                add(json.loads(value))
    return found


def backup_database(
    db_path: Path | str,
    dest_dir: Path | str,
    *,
    keep: int,
    now: datetime | None = None,
) -> Path | None:
    """Согласованная копия SQLite через backup API (безопасно при работающем боте)."""
    src = Path(db_path)
    if not src.is_file():
        LOGGER.warning("db backup skipped: %s not found", src)
        return None
    dest = Path(dest_dir)
    if dest.is_symlink():
        raise ValueError("unsafe_backup_directory")
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    dest.chmod(0o700)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    target = dest / f"{src.stem}-{stamp}.db"
    tmp = target.with_suffix(".db.tmp")
    if (
        target.is_symlink()
        or tmp.is_symlink()
        or target.resolve().parent != dest
        or tmp.resolve().parent != dest
    ):
        raise ValueError("unsafe_backup_target")
    tmp.touch(mode=0o600, exist_ok=True)
    tmp.chmod(0o600)
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(str(tmp))
    try:
        source.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("backup_integrity_failed")
    finally:
        copy.close()
        source.close()
    tmp.replace(target)
    target.chmod(0o600)

    backups = sorted(dest.glob(f"{src.stem}-*.db"))
    for old in backups[: max(0, len(backups) - max(1, keep))]:
        try:
            if old.is_symlink() or old.resolve().parent != dest:
                continue
            old.unlink()
        except OSError:
            LOGGER.warning("db backup rotate failed path=%s", old)
    return target


def run_maintenance() -> dict[str, object]:
    try:
        protected = referenced_downloads(config.DB_PATH)
    except (OSError, sqlite3.Error, ValueError) as exc:
        LOGGER.warning(
            "maintenance cleanup skipped: reference inventory error_type=%s",
            type(exc).__name__,
        )
        return {
            "downloads_removed": 0,
            "downloads_freed_mb": 0,
            "db_backup": None,
            "cleanup_skipped": True,
        }
    backup = backup_database(
        config.DB_PATH, config.DB_BACKUP_DIR, keep=int(config.DB_BACKUP_KEEP)
    )
    removed, freed = cleanup_downloads(
        days=int(config.DOWNLOADS_RETENTION_DAYS), protected=protected
    )
    result = {
        "downloads_removed": removed,
        "downloads_freed_mb": round(freed / 1024 / 1024, 1),
        "db_backup": str(backup) if backup else None,
    }
    LOGGER.info("maintenance done %s", result)
    return result


__all__ = ["cleanup_downloads", "backup_database", "run_maintenance"]
