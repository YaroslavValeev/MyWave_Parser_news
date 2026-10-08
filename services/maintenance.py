"""Ежедневное обслуживание: очистка старых медиа и резервная копия SQLite."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from config.settings import config
from utils.media_utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

LOGGER = logging.getLogger(__name__)

_MEDIA_SUFFIXES = {ext.lower() for ext in (*IMAGE_EXTENSIONS, *VIDEO_EXTENSIONS)}


def cleanup_downloads(root: Path | str = "downloads", *, days: int) -> tuple[int, int]:
    """Удалить медиафайлы старше ``days`` дней. Возвращает (файлов, байт).

    Через 30 дней материал уже опубликован или снят с ревью; копия медиа
    остаётся на сайте, а публикация умеет докачать файл по public_url.
    """
    base = Path(root)
    if days <= 0 or not base.is_dir():
        return 0, 0
    cutoff = time.time() - days * 86400
    removed = freed = 0
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _MEDIA_SUFFIXES:
            continue
        try:
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
            freed += stat.st_size
        except OSError as exc:
            LOGGER.warning("downloads cleanup skip path=%s error=%s", path, type(exc).__name__)
    return removed, freed


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
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    target = dest / f"{src.stem}-{stamp}.db"
    tmp = target.with_suffix(".db.tmp")
    source = sqlite3.connect(str(src))
    copy = sqlite3.connect(str(tmp))
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    tmp.replace(target)

    backups = sorted(dest.glob(f"{src.stem}-*.db"))
    for old in backups[: max(0, len(backups) - max(1, keep))]:
        try:
            old.unlink()
        except OSError:
            LOGGER.warning("db backup rotate failed path=%s", old)
    return target


def run_maintenance() -> dict[str, object]:
    removed, freed = cleanup_downloads(days=int(config.DOWNLOADS_RETENTION_DAYS))
    backup = backup_database(config.DB_PATH, config.DB_BACKUP_DIR, keep=int(config.DB_BACKUP_KEEP))
    result = {
        "downloads_removed": removed,
        "downloads_freed_mb": round(freed / 1024 / 1024, 1),
        "db_backup": str(backup) if backup else None,
    }
    LOGGER.info("maintenance done %s", result)
    return result


__all__ = ["cleanup_downloads", "backup_database", "run_maintenance"]
