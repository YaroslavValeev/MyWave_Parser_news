"""Единая материализация медиа после сбора.

Источник (Telegram / RSS og:image / enclosure) → локальный файл в downloads/
→ POST /api/media/upload → public_url в items.images + raw_feed.

Дальше все потребители берут медиа из item:
- сайт — public_url из raw_feed;
- карточка ревью и публикация в группу — локальный файл (FSInputFile),
  а при его отсутствии public_url / скачивание Parser'ом.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from config.settings import config

LOGGER = logging.getLogger(__name__)

DONE_LOG = "media_hydrate_done"
FAILED_LOG = "media_hydrate_failed"
_STATUSES = ("new", "review", "deferred", "ready_to_publish")
_warned_not_configured = False


def _has_media(item: Mapping[str, Any]) -> bool:
    return any(str(item.get(key) or "").strip() for key in ("images", "videos"))


async def _attempts_so_far(repo: Any, item_id: int) -> int:
    last = await repo.get_last_log(item_id, FAILED_LOG)
    if not last:
        return 0
    meta = last.get("meta") or {}
    try:
        return int(meta.get("attempt") or 1)
    except (TypeError, ValueError):
        return 1


async def hydrate_item(repo: Any, item_id: int) -> bool:
    """Подготовить медиа одной новости. True — обработка завершена (медиа есть или его нет в источнике)."""
    from services.site_media_client import maybe_autoupload_local_cover_and_sync_sheet

    result = await maybe_autoupload_local_cover_and_sync_sheet(repo, item_id, trigger="media_pipeline")
    item = await repo.get_item(item_id) or {}
    upload_failed = result is not None and not result.ok
    if upload_failed:
        attempt = await _attempts_so_far(repo, item_id) + 1
        await repo.log_event(
            item_id,
            "warning",
            FAILED_LOG,
            {"attempt": attempt, "error": result.error, "status_code": result.status_code},
        )
        if attempt < max(1, config.MEDIA_HYDRATE_MAX_ATTEMPTS):
            return False
    await repo.log_event(
        item_id,
        "info",
        DONE_LOG,
        {
            "has_media": _has_media(item),
            "uploaded": bool(result is not None and result.ok),
            "upload_failed": upload_failed,
        },
    )
    return True


async def run_media_hydrate(repo: Any, *, batch: int | None = None) -> int:
    """Обработать до ``batch`` свежих новостей без материализованного медиа."""
    from services.site_media_client import media_upload_configured

    global _warned_not_configured
    if not media_upload_configured():
        if not _warned_not_configured:
            LOGGER.warning("media_pipeline disabled: MEDIA_UPLOAD_URL/MEDIA_UPLOAD_TOKEN not set")
            _warned_not_configured = True
        return 0
    limit = max(1, batch or config.MEDIA_HYDRATE_BATCH)
    candidates = await repo.list_items_by_status(_STATUSES, limit=limit * 20, order="DESC")
    if not candidates:
        LOGGER.info("media_pipeline: no items in statuses %s", ",".join(_STATUSES))
    processed = 0
    for item in candidates:
        if processed >= limit:
            break
        item_id = int(item["id"])
        if await repo.get_last_log(item_id, DONE_LOG):
            continue
        processed += 1
        try:
            await hydrate_item(repo, item_id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("media_pipeline failed item_id=%s", item_id)
    if processed:
        LOGGER.info("media_pipeline processed=%s", processed)
    return processed


__all__ = ["run_media_hydrate", "hydrate_item", "DONE_LOG", "FAILED_LOG"]
