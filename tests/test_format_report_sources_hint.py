"""Отчёт /report: подсказка по логам RSS/YouTube."""
from __future__ import annotations

from telegram_bot.views import format_report


def test_format_report_includes_rss_youtube_hint():
    text = format_report(
        {"new": 1},
        {"nlp_pending": 1, "nlp_processing": 0},
        publication_pending=0,
        channel_configured=True,
    )
    assert "HTTP_FEED_PROXY" in text
    assert "RSS" in text or "YouTube" in text
