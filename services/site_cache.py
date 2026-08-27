"""Cache invalidation client for the MyWave site."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urljoin

import requests

from config.settings import config

LOGGER = logging.getLogger(__name__)


def cache_invalidate_url() -> str:
    endpoint = str(getattr(config, "SITE_CACHE_INVALIDATE_ENDPOINT", "") or "").strip()
    if not endpoint:
        return ""
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    base_url = str(getattr(config, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return ""
    return urljoin(base_url + "/", endpoint.lstrip("/"))


def cache_invalidate_configured() -> bool:
    token = str(getattr(config, "SITE_CACHE_INVALIDATE_TOKEN", "") or "").strip()
    return bool(cache_invalidate_url() and token)


def _invalidate_sync(*, item_id: int | None = None, reason: str = "") -> bool:
    if not cache_invalidate_configured():
        return False
    url = cache_invalidate_url()
    token = str(getattr(config, "SITE_CACHE_INVALIDATE_TOKEN", "") or "").strip()
    timeout = float(getattr(config, "SITE_CACHE_INVALIDATE_TIMEOUT_SECONDS", 15))
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {}
    if item_id:
        payload["item_id"] = str(item_id)
    if reason:
        payload["reason"] = reason
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        LOGGER.warning("site cache invalidate request failed: %s", exc)
        return False
    if response.status_code not in {200, 201, 202, 204}:
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = response.text
        LOGGER.warning(
            "site cache invalidate failed status=%s body=%s",
            response.status_code,
            body,
        )
        return False
    return True


async def invalidate_site_blog_cache(*, item_id: int | None = None, reason: str = "") -> bool:
    return await asyncio.to_thread(_invalidate_sync, item_id=item_id, reason=reason)


__all__ = [
    "cache_invalidate_configured",
    "cache_invalidate_url",
    "invalidate_site_blog_cache",
]
