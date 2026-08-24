"""Русские подписи статусов в Telegram UI."""
from __future__ import annotations

from telegram_bot.views import format_stats


def test_format_stats_uses_russian_status_labels():
    text = format_stats(
        {"review": 8, "deferred": 3, "discarded": 61, "error": 1, "expired": 3, "published": 8},
        {"nlp_pending": 0, "nlp_processing": 0},
    )
    assert "на ревью: 8" in text
    assert "отложено: 3" in text
    assert "deferred:" not in text
    assert "Очередь NLP" in text
    assert "Ожидают обработки" in text
    assert "Source health" in text
