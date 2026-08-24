"""APScheduler integration for periodic background tasks."""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Optional

from aiogram import Bot
from apscheduler.events import EVENT_JOB_MAX_INSTANCES
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone

from config.settings import config
from core.scheduler import ParseAllSourcesBusyError, parse_all_sources
from services.nlp_pipeline import process_nlp_queue
from services.publication import PublicationService
from storage.repository import AsyncNewsRepository
from telegram_bot.views import format_stats

from services.owner_audit_export import run_owner_audit_export
from services.competitions_ticker_sync import archive_past_competitions
from services.channel_engagement import run_channel_engagement

LOGGER = logging.getLogger(__name__)


class SchedulerService:
    """Configure and manage periodic background jobs."""

    def __init__(self, repository: AsyncNewsRepository, bot: Bot) -> None:
        tz = timezone(str(config.SCHEDULER_TIMEZONE or "UTC"))
        self._scheduler = AsyncIOScheduler(
            timezone=tz,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 120,
            },
        )
        self._repository = repository
        self._bot = bot
        self._publication_service = PublicationService(repository, bot, config.CHANNEL_ID)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        collect_trigger, collect_job_kwargs = self._build_collect_trigger()
        self._scheduler.add_job(
            self._collect_sources_job,
            collect_trigger,
            name="collect_sources",
            id="collect_sources",
            replace_existing=True,
            **collect_job_kwargs,
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
        if getattr(config, "OWNER_AUDIT_EXPORT_ENABLED", False):
            self._scheduler.add_job(
                self._owner_audit_export_job,
                IntervalTrigger(
                    minutes=max(5, int(getattr(config, "OWNER_AUDIT_EXPORT_INTERVAL_MINUTES", 60)))
                ),
                name="owner_audit_export",
                id="owner_audit_export",
                replace_existing=True,
            )
        if getattr(config, "COMPETITIONS_SYNC_ENABLED", True):
            self._scheduler.add_job(
                self._competitions_archive_job,
                CronTrigger(
                    hour=int(getattr(config, "COMPETITIONS_ARCHIVE_HOUR", 3)),
                    minute=int(getattr(config, "COMPETITIONS_ARCHIVE_MINUTE", 15)),
                ),
                name="competitions_archive",
                id="competitions_archive",
                replace_existing=True,
            )
        if getattr(config, "ENGAGEMENT_COLLECT_ENABLED", False):
            self._scheduler.add_job(
                self._collect_engagement_job,
                CronTrigger(
                    hour=int(getattr(config, "ENGAGEMENT_CRON_HOUR", 4)),
                    minute=int(getattr(config, "ENGAGEMENT_CRON_MINUTE", 30)),
                ),
                name="collect_engagement",
                id="collect_engagement",
                replace_existing=True,
            )
        if getattr(config, "COMPETITIONS_COLLECT_ENABLED", False):
            self._scheduler.add_job(
                self._competitions_collect_job,
                CronTrigger(
                    hour=int(getattr(config, "COMPETITIONS_ARCHIVE_HOUR", 3)),
                    minute=int(getattr(config, "COMPETITIONS_ARCHIVE_MINUTE", 45)),
                ),
                name="competitions_collect",
                id="competitions_collect",
                replace_existing=True,
            )
        self._scheduler.add_listener(self._on_job_max_instances, EVENT_JOB_MAX_INSTANCES)
        self._scheduler.start()
        self._started = True
        LOGGER.info("Scheduler started with jobs: %s", [job.id for job in self._scheduler.get_jobs()])

    def _build_collect_trigger(self):
        mode = str(getattr(config, "COLLECT_SCHEDULE_MODE", "daily") or "daily").strip().lower()
        if mode == "interval":
            return (
                IntervalTrigger(minutes=max(1, config.COLLECT_INTERVAL_MINUTES)),
                {},
            )
        return (
            CronTrigger(
                hour=int(getattr(config, "COLLECT_DAILY_HOUR", 12)),
                minute=int(getattr(config, "COLLECT_DAILY_MINUTE", 0)),
            ),
            {
                "misfire_grace_time": max(
                    120,
                    int(getattr(config, "COLLECT_MISFIRE_GRACE_SECONDS", 12 * 60 * 60)),
                ),
            },
        )

    def _on_job_max_instances(self, event) -> None:
        """Пояснение к WARNING APScheduler про max_instances (длинный сбор)."""
        if getattr(event, "job_id", None) != "collect_sources":
            return
        LOGGER.info(
            "Планировщик: collect_sources не запущен — предыдущий полный сбор ещё выполняется. "
            "Если сообщение повторяется часто, увеличьте окно расписания/лимит сбора (режим=%s).",
            getattr(config, "COLLECT_SCHEDULE_MODE", "daily"),
        )

    async def shutdown(self) -> None:
        if not self._started:
            return
        # APScheduler 3.x: shutdown() синхронный; в других версиях может вернуть корутину.
        shutdown_fn = self._scheduler.shutdown
        if inspect.iscoroutinefunction(shutdown_fn):
            await shutdown_fn(wait=False)
        else:
            maybe = shutdown_fn(wait=False)
            if asyncio.iscoroutine(maybe):
                await maybe
        self._started = False
        LOGGER.info("Scheduler stopped")

    async def _collect_sources_job(self) -> None:
        try:
            report = await parse_all_sources()
            LOGGER.info(
                "collect_sources job finished, news_saved=%s contacts=%s in %.1fs (sources ok %s/%s)",
                report.news_saved,
                report.contacts_saved,
                report.elapsed_seconds,
                report.sources_ok,
                report.sources_total,
            )
            if report.sources_failed:
                await self._notify_collect_failures(report)
        except ParseAllSourcesBusyError:
            LOGGER.info(
                "collect_sources skipped: parse_all_sources already running (manual /parse or overlap)"
            )
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

    async def _owner_audit_export_job(self) -> None:
        try:
            result = await run_owner_audit_export(self._repository)
            if result.exported:
                LOGGER.info(
                    "owner_audit_export: exported=%s sheets=%s csv=%s cursor=%s err=%s",
                    result.exported,
                    result.sheets_written,
                    result.csv_path,
                    result.new_cursor,
                    result.error,
                )
            elif result.error:
                LOGGER.warning("owner_audit_export: %s", result.error)
        except Exception:  # noqa: BLE001
            LOGGER.exception("owner_audit_export job failed")

    async def _competitions_archive_job(self) -> None:
        try:
            n = await archive_past_competitions()
            if n:
                LOGGER.info("competitions_archive job archived=%s rows", n)
        except Exception:  # noqa: BLE001
            LOGGER.exception("competitions_archive job failed")

    async def _collect_engagement_job(self) -> None:
        try:
            result = await run_channel_engagement(sync_sheet=True)
            LOGGER.info(
                "collect_engagement job saved_db=%s sheet_up=%s sheet_app=%s comments=%s",
                result.saved_db,
                result.sheet_updated,
                result.sheet_appended,
                result.stats.comments_collected if result.stats else 0,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("collect_engagement job failed")

    async def _competitions_collect_job(self) -> None:
        try:
            from services.competitions_sources_collect import collect_competitions_from_sources

            stats = await collect_competitions_from_sources()
            LOGGER.info("competitions_collect job stats=%s", stats)
        except Exception:  # noqa: BLE001
            LOGGER.exception("competitions_collect job failed")

    async def _daily_stats_job(self) -> None:
        chat_id = self._resolve_editors_chat_id()
        if chat_id is None:
            LOGGER.debug("EDITORS_CHAT_ID is not configured, skip daily stats")
            return
        counts = await self._repository.get_status_counts()
        if not counts:
            return
        metrics = await self._repository.get_processing_summary()
        LOGGER.info(
            "daily_stats: items_by_status=%s nlp_pending=%s nlp_processing=%s",
            counts,
            metrics.get("nlp_pending"),
            metrics.get("nlp_processing"),
        )
        text = format_stats(counts, metrics)
        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            LOGGER.exception("daily_stats job failed to send message")

    async def _notify_collect_failures(self, report) -> None:
        chat_id = self._resolve_editors_chat_id()
        if chat_id is None:
            return
        from utils.collect_report import format_collect_report_html, load_collect_report

        text = (
            "<b>Сбор источников: есть ошибки</b>\n"
            f"Успешно {report.sources_ok}/{report.sources_total}, ошибок {report.sources_failed}."
            f"{format_collect_report_html(load_collect_report())}"
        )
        try:
            await self._bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            LOGGER.exception("collect failure alert failed to send")

    def _resolve_editors_chat_id(self) -> Optional[int | str]:
        if not config.EDITORS_CHAT_ID:
            return None
        try:
            return int(config.EDITORS_CHAT_ID)
        except (TypeError, ValueError):
            return config.EDITORS_CHAT_ID


__all__ = ["SchedulerService"]
