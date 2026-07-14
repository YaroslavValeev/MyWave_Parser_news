"""Проверка прав владельца (без Telegram)."""
from __future__ import annotations

from config.settings import config
from telegram_bot.access import is_bot_operator


def test_is_bot_operator_none_denied():
    assert is_bot_operator(None) is False


def test_is_bot_operator_empty_owner_allows_all(monkeypatch):
    monkeypatch.setattr(config, "OWNER_USER_ID", "", raising=False)
    assert is_bot_operator(999001) is True


def test_is_bot_operator_list_filtered(monkeypatch):
    monkeypatch.setattr(config, "OWNER_USER_ID", "1, 42 , 100", raising=False)
    assert is_bot_operator(42) is True
    assert is_bot_operator(43) is False
