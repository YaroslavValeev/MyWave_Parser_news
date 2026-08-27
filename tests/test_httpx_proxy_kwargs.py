"""Совместимость httpx AsyncClient proxy=/proxies= для OpenAI SOCKS."""
from __future__ import annotations

import inspect

import httpx
import pytest

from nlp.openai_client import _httpx_proxy_client_kwargs


def test_httpx_proxy_kwargs_matches_installed_signature():
    url = "socks5://user:pass@127.0.0.1:1080"
    kwargs = _httpx_proxy_client_kwargs(url)
    params = inspect.signature(httpx.AsyncClient.__init__).parameters
    if "proxy" in params:
        assert kwargs == {"proxy": url}
    elif "proxies" in params:
        assert kwargs == {"proxies": url}
    else:
        pytest.fail(f"unexpected httpx signature: {httpx.__version__}")


def test_httpx_proxy_kwargs_empty_url():
    assert _httpx_proxy_client_kwargs("") == {}
    assert _httpx_proxy_client_kwargs("   ") == {}


def test_openai_client_prompts_are_valid_cyrillic():
    import inspect

    from nlp.openai_client import OpenAIClient

    src = inspect.getsource(OpenAIClient)
    assert "Сделай краткое новостное резюме" in src
    assert "Сформулируй" in src
    assert "РЎРґРµР»Р°Р№" not in src
