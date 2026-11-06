"""Сброс кэша ticker соревнований на сайте mywavewake.ru."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urljoin

import requests

from config.settings import config

LOGGER = logging.getLogger(__name__)


def competitions_cache_invalidate_url() -> str:
    endpoint = str(getattr(config, "COMPETITIONS_CACHE_INVALIDATE_ENDPOINT", "") or "").strip()
    if not endpoint:
        endpoint = "/api/competitions/cache/invalidate"
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    base_url = str(getattr(config, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return ""
    return urljoin(base_url + "/", endpoint.lstrip("/"))


def competitions_cache_invalidate_token() -> str:
    return (
        str(getattr(config, "COMPETITIONS_CACHE_INVALIDATE_TOKEN", "") or "").strip()
        or str(getattr(config, "SITE_CACHE_INVALIDATE_TOKEN", "") or "").strip()
        or str(getattr(config, "MEDIA_UPLOAD_TOKEN", "") or "").strip()
        or str(getattr(config, "SITE_MEDIA_UPLOAD_TOKEN", "") or "").strip()
    )


def competitions_cache_invalidate_configured() -> bool:
    return bool(competitions_cache_invalidate_url() and competitions_cache_invalidate_token())


def _invalidate_sync(*, reason: str = "competitions_ticker_sync") -> bool:
    if not competitions_cache_invalidate_configured():
        LOGGER.debug("competitions cache invalidate skipped: not configured")
        return False
    url = competitions_cache_invalidate_url()
    token = competitions_cache_invalidate_token()
    timeout = float(getattr(config, "COMPETITIONS_CACHE_INVALIDATE_TIMEOUT_SECONDS", 15))
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Media-Upload-Token": token,
        "Content-Type": "application/json",
    }
    payload: dict[str, str] = {"reason": reason}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        LOGGER.warning("competitions cache invalidate request failed: %s", exc)
        return False
    if response.status_code not in {200, 201, 202, 204}:
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = response.text
        LOGGER.warning(
            "competitions cache invalidate failed status=%s body=%s",
            response.status_code,
            body,
        )
        return False
    LOGGER.info("competitions cache invalidated reason=%s", reason)
    return True


async def invalidate_competitions_cache(*, reason: str = "competitions_ticker_sync") -> bool:
    return await asyncio.to_thread(_invalidate_sync, reason=reason)


__all__ = [
    "competitions_cache_invalidate_configured",
    "competitions_cache_invalidate_url",
    "invalidate_competitions_cache",
]
