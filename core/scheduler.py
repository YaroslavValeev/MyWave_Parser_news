"""Collection entry points used by background scheduler."""
from __future__ import annotations

import asyncio
import logging

from config.settings import config
from services.manual_collect import ManualSource, _fetch_items
from storage.data import save_contacts, save_news
from storage.sources import list_sources

LOGGER = logging.getLogger(__name__)


async def parse_all_sources() -> int:
    """Collect all configured sources and persist news and contacts."""

    total_news_saved = 0
    total_contacts_saved = 0

    for source in list_sources():
        manual_source = ManualSource(
            type=source.type,
            url=source.url,
            name=source.name or source.url,
        )
        try:
            items, contacts = await _fetch_items(
                manual_source,
                limit=getattr(config, "MAX_MESSAGES", None),
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to collect source %s", source.url)
            continue

        if items:
            saved = await save_news(items)
            total_news_saved += saved
            LOGGER.debug(
                "Saved %s news items from %s (%s)",
                saved,
                manual_source.name,
                manual_source.type,
            )
        if contacts:
            stored_contacts = await save_contacts(contacts)
            total_contacts_saved += stored_contacts
            LOGGER.debug(
                "Saved %s contacts from %s", stored_contacts, manual_source.url
            )

    LOGGER.info(
        "Collection finished: news_saved=%s contacts_saved=%s",
        total_news_saved,
        total_contacts_saved,
    )
    return total_news_saved


def run_scheduler(interval_hours: float | None = None) -> None:
    """Legacy helper to execute collection on a simple interval."""

    interval = interval_hours or max(float(config.PARSING_INTERVAL) / 3600, 0.1)

    async def _runner() -> None:
        while True:
            await parse_all_sources()
            await asyncio.sleep(interval * 3600)

    asyncio.run(_runner())


__all__ = ["parse_all_sources", "run_scheduler"]
