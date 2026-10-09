"""Лёгкие проверки вспомогательных функций роутера (без живого Telegram)."""
from __future__ import annotations

import time

import pytest

from telegram_bot import router as router_mod


def test_mask_token_empty_and_masked():
    assert router_mod._mask_token("") == "не задан"
    assert router_mod._mask_token("abcd") == "****"
    out = router_mod._mask_token("abcdefghijklmnop")
    assert "…" in out
    assert "len=" in out


def test_format_elapsed_minutes():
    assert router_mod._format_elapsed_minutes(59) == "59 мин"
    assert "ч" in router_mod._format_elapsed_minutes(120)


def test_help_html_contains_key_commands():
    assert "/parse" in router_mod.HELP_HTML
    assert "/requeue_nlp" in router_mod.HELP_HTML


def test_requeue_rate_limits(monkeypatch):
    router_mod._requeue_last_by_user.clear()
    router_mod._requeue_calls_hour.clear()
    try:
        monkeypatch.setattr(
            router_mod.config,
            "REQUEUE_NLP_COOLDOWN_SECONDS",
            10,
            raising=False,
        )
        monkeypatch.setattr(
            router_mod.config,
            "REQUEUE_NLP_MAX_PER_HOUR",
            2,
            raising=False,
        )
        uid = 777777
        ok, _ = router_mod._requeue_rate_check(uid)
        assert ok is True
        router_mod._requeue_last_by_user[uid] = time.time()
        ok2, msg = router_mod._requeue_rate_check(uid)
        assert ok2 is False
        assert "Подождите" in msg
    finally:
        router_mod._requeue_last_by_user.clear()
        router_mod._requeue_calls_hour.clear()


def test_parse_schedule_placeholder():
    utc, err = router_mod._parse_schedule_local_to_utc("")
    assert utc is None and err

