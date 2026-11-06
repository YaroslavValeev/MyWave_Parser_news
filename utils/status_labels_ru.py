"""Русские подписи внутренних статусов для owner-facing Telegram UI."""
from __future__ import annotations

from typing import Mapping

# Ключи — значения колонки status в SQLite; значения — текст для владельца.
STATUS_LABELS_RU: dict[str, str] = {
    "new": "новые",
    "processing": "в обработке",
    "review": "на ревью",
    "approved": "одобрено",
    "ready_to_publish": "готово к публикации",
    "publish_retry": "повтор публикации",
    "published": "опубликовано",
    "deferred": "отложено",
    "discarded": "отклонено",
    "error": "ошибка NLP",
    "expired": "истекло (старше лимита ревью)",
}


def status_label_ru(status: str | None) -> str:
    key = str(status or "").strip().lower()
    if not key:
        return "—"
    return STATUS_LABELS_RU.get(key, key)


def format_status_counts_ru(counts: Mapping[str, int]) -> list[str]:
    """Строки «подпись: N» для сводок /stats и /report."""
    order = (
        "review",
        "new",
        "deferred",
        "approved",
        "ready_to_publish",
        "publish_retry",
        "published",
        "error",
        "expired",
        "discarded",
        "processing",
    )
    lines: list[str] = []
    seen: set[str] = set()
    for key in order:
        if key in counts:
            lines.append(f"{status_label_ru(key)}: {counts[key]}")
            seen.add(key)
    for key in sorted(counts):
        if key not in seen:
            lines.append(f"{status_label_ru(key)}: {counts[key]}")
    return lines


__all__ = ["STATUS_LABELS_RU", "format_status_counts_ru", "status_label_ru"]
