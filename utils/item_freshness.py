"""Фильтрация материалов по дате публикации для очереди ревью."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from config.settings import config


def review_max_age_days() -> int:
    """Сколько дней считать материал «свежим» для ревью (0 = фильтр выключен)."""
    try:
        return max(0, int(getattr(config, "REVIEW_MAX_AGE_DAYS", 30)))
    except (TypeError, ValueError):
        return 30


def parse_item_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif value is None:
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
                try:
                    dt = datetime.strptime(text[: len(fmt)], fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_item_date(value: object) -> str | None:
    dt = parse_item_datetime(value)
    return dt.isoformat() if dt else None


def item_publication_datetime(item: Mapping[str, Any]) -> datetime | None:
    """Дата публикации материала (не дата ingest в БД)."""
    for key in ("date", "original_published_at", "published_at"):
        raw = item.get(key)
        if raw:
            parsed = parse_item_datetime(raw)
            if parsed:
                return parsed
    return None


def is_item_stale_for_review(
    item: Mapping[str, Any],
    *,
    max_days: int | None = None,
    now: datetime | None = None,
) -> bool:
    """True, если материал вышел раньше порога (по умолчанию 30 дней)."""
    limit = review_max_age_days() if max_days is None else max(0, int(max_days))
    if limit <= 0:
        return False
    published = item_publication_datetime(item)
    if published is None:
        return False
    ref = now or datetime.now(timezone.utc)
    cutoff = ref - timedelta(days=limit)
    return published < cutoff


def review_stale_cutoff(
    *,
    max_days: int | None = None,
    now: datetime | None = None,
) -> datetime:
    limit = review_max_age_days() if max_days is None else max(0, int(max_days))
    ref = now or datetime.now(timezone.utc)
    return ref - timedelta(days=limit)


__all__ = [
    "is_item_stale_for_review",
    "item_publication_datetime",
    "normalize_item_date",
    "parse_item_datetime",
    "review_max_age_days",
    "review_stale_cutoff",
]
