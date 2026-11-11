"""Лимиты частоты /requeue_nlp."""
from __future__ import annotations

import time

import pytest

from config.settings import config
from telegram_bot import router as router_mod


@pytest.fixture(autouse=True)
def reset_requeue_state():
    router_mod._requeue_last_by_user.clear()
    router_mod._requeue_calls_hour.clear()
    yield
    router_mod._requeue_last_by_user.clear()
    router_mod._requeue_calls_hour.clear()


def test_requeue_cooldown(monkeypatch):
    monkeypatch.setattr(config, "REQUEUE_NLP_COOLDOWN_SECONDS", 60)
    monkeypatch.setattr(config, "REQUEUE_NLP_MAX_PER_HOUR", 10)
    ok, _ = router_mod._requeue_rate_check(1)
    assert ok
    router_mod._requeue_last_by_user[1] = time.time()
    ok2, msg = router_mod._requeue_rate_check(1)
    assert not ok2
    assert "Подождите" in msg


def test_requeue_hourly_cap(monkeypatch):
    monkeypatch.setattr(config, "REQUEUE_NLP_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(config, "REQUEUE_NLP_MAX_PER_HOUR", 2)
    now = time.time()
    router_mod._requeue_calls_hour.append((now, 42))
    router_mod._requeue_calls_hour.append((now, 42))
    ok, msg = router_mod._requeue_rate_check(42)
    assert not ok
    assert "Лимит" in msg
