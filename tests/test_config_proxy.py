"""Проверки подсказок и URL прокси для Bot API (без реальной сети)."""

from __future__ import annotations

import importlib
import os
import sys


def _reload_settings(monkeypatch, **env: str) -> None:
    # Иначе load_dotenv(.env) перезапишет переменные (напр. BOT_API_PROXY_URL с хоста).
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config.settings", None)
    import config.settings as settings

    importlib.reload(settings)


def test_bot_proxy_endpoint_hint_from_dedicated_url(monkeypatch) -> None:
    _reload_settings(monkeypatch, BOT_API_PROXY_URL="socks5://127.0.0.1:7891")
    from config.settings import config

    assert config.bot_proxy_endpoint_hint() == "127.0.0.1:7891 (socks5)"


def test_bot_proxy_endpoint_hint_dedicated_url_default_port(monkeypatch) -> None:
    _reload_settings(monkeypatch, BOT_API_PROXY_URL="socks5://127.0.0.1")
    from config.settings import config

    assert config.bot_proxy_endpoint_hint() == "127.0.0.1:1080 (socks5)"


def test_bot_proxy_endpoint_hint_from_proxy_host(monkeypatch) -> None:
    monkeypatch.delenv("BOT_API_PROXY_URL", raising=False)
    _reload_settings(
        monkeypatch,
        PROXY_ENABLED="true",
        PROXY_HOST="10.0.0.5",
        PROXY_PORT="1080",
        PROXY_TYPE="socks5",
    )
    from config.settings import config

    assert config.bot_proxy_endpoint_hint() == "10.0.0.5:1080 (socks5)"


def test_bot_api_dedicated_overrides_for_primary_url(monkeypatch) -> None:
    _reload_settings(
        monkeypatch,
        BOT_API_PROXY_URL="socks5://127.0.0.1:1",
        PROXY_ENABLED="true",
        PROXY_HOST="dead.example",
        PROXY_PORT="1080",
        BOT_API_USE_PROXY="true",
    )
    from config.settings import config

    assert config.bot_api_dedicated_proxy_url() == "socks5://127.0.0.1:1"
    dedicated = config.bot_api_dedicated_proxy_url()
    primary = dedicated or config.bot_api_proxy_url()
    assert primary == "socks5://127.0.0.1:1"
