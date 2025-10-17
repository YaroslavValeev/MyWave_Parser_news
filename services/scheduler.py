"""APScheduler integration for periodic background tasks."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone

from config.settings import config
from core.scheduler import parse_all_sources
from services.nlp_pipeline import process_nlp_queue
from services.publication import PublicationService
from storage.repository import AsyncNewsRepository
from telegram_bot.views import format_stats

LOGGER = logging.getLogger(__name__)


class SchedulerService:
    """Configure and manage periodic background jobs."""

    def __init__(self, repository: AsyncNewsRepository, bot: Bot) -> None:
        tz = timezone(str(config.SCHEDULER_TIMEZONE or "UTC"))
        self._scheduler = AsyncIOScheduler(timezone=tz)
        self._repository = repository
        self._bot = bot
        self._publication_service = PublicationService(repository, bot, config.CHANNEL_ID)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._scheduler.add_job(
            self._collect_sources_job,
            IntervalTrigger(minutes=max(1, config.COLLECT_INTERVAL_MINUTES)),
            name="collect_sources",
            id="collect_sources",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._process_nlp_job,
            IntervalTrigger(minutes=max(1, config.NLP_INTERVAL_MINUTES)),
            name="process_nlp",
            id="process_nlp",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._retry_publications_job,
            IntervalTrigger(minutes=max(1, config.RETRY_PUBLICATIONS_INTERVAL_MINUTES)),
            name="retry_publications",
            id="retry_publications",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._daily_stats_job,
            CronTrigger(hour=config.DAILY_STATS_HOUR, minute=config.DAILY_STATS_MINUTE),
            name="daily_stats",
            id="daily_stats",
            replace_existing=True,
        )
        self._scheduler.start()
        self._started = True
        LOGGER.info("Scheduler started with jobs: %s", [job.id for job in self._scheduler.get_jobs()])

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self._scheduler.shutdown(wait=False)
        self._started = False
        LOGGER.info("Scheduler stopped")

    async def _collect_sources_job(self) -> None:
        try:
            saved = await parse_all_sources()
            LOGGER.info("collect_sources job finished, saved=%s", saved)
        except Exception:  # noqa: BLE001
            LOGGER.exception("collect_sources job failed")

    async def _process_nlp_job(self) -> None:
        try:
            processed = await process_nlp_queue(repository=self._repository)
            LOGGER.info("process_nlp job finished, processed=%s", processed)
        except Exception:  # noqa: BLE001
            LOGGER.exception("process_nlp job failed")

    async def _retry_publications_job(self) -> None:
        try:
            published = await self._publication_service.publish_pending()
            if published:
                LOGGER.info("retry_publications job published %s items", published)
        except Exception:  # noqa: BLE001
            LOGGER.exception("retry_publications job failed")

    async def _daily_stats_job(self) -> None:
        chat_id = self._resolve_editors_chat_id()
        if chat_id is None:
            LOGGER.debug("EDITORS_CHAT_ID is not configured, skip daily stats")
            return
        counts = await self._repository.get_status_counts()
        if not counts:
            return
        metrics = await self._repository.get_processing_summary()
        text = format_stats(counts, metrics)
        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            LOGGER.exception("daily_stats job failed to send message")

    def _resolve_editors_chat_id(self) -> Optional[int | str]:
        if not config.EDITORS_CHAT_ID:
            return None
        try:
            return int(config.EDITORS_CHAT_ID)
        except (TypeError, ValueError):
            return config.EDITORS_CHAT_ID


__all__ = ["SchedulerService"]
