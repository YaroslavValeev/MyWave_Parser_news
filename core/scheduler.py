"""Collection entry points used by background scheduler."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from config.settings import config
from services.manual_collect import ManualSource, _fetch_items
from services.source_telemetry import SourceTickMetrics
from storage.data import get_repository, save_contacts, save_news_detailed
from storage.sources import list_sources
from utils.collect_report import save_collect_report

LOGGER = logging.getLogger(__name__)

# Один активный полный обход: параллельно bot + APScheduler давали два Telethon-потока и обрывы сети.
_parse_all_lock = asyncio.Lock()


def _chunk_index_path() -> Path:
    db_path = Path(config.DB_PATH)
    parent = db_path.parent if db_path.parent.parts else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    return parent / ".collect_chunk_index"


def _read_collect_chunk_index() -> int:
    path = _chunk_index_path()
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_collect_chunk_index(value: int) -> None:
    _chunk_index_path().write_text(str(value), encoding="utf-8")


def _slice_sources_for_chunk(sources: list) -> tuple[list, str]:
    """При COLLECT_SOURCES_CHUNK_SIZE>0 — round-robin подмножество источников за один тик."""
    chunk = max(0, int(getattr(config, "COLLECT_SOURCES_CHUNK_SIZE", 0) or 0))
    if chunk <= 0 or not sources:
        return sources, "full"
    n = len(sources)
    num_chunks = max(1, (n + chunk - 1) // chunk)
    idx = _read_collect_chunk_index() % num_chunks
    start = idx * chunk
    end = min(start + chunk, n)
    subset = sources[start:end]
    _write_collect_chunk_index(idx + 1)
    LOGGER.info(
        "collect_sources: chunk %s/%s (источники %s..%s из %s, размер чанка=%s)",
        idx + 1,
        num_chunks,
        start,
        end,
        n,
        chunk,
    )
    return subset, f"chunk_{idx + 1}_of_{num_chunks}"


class ParseAllSourcesBusyError(RuntimeError):
    """Уже выполняется другой вызов parse_all_sources (ручной или по расписанию)."""


def parse_all_sources_busy() -> bool:
    """Best-effort: True, если сейчас удерживается lock полного сбора (без ожидания)."""
    return _parse_all_lock.locked()


@dataclass(slots=True, frozen=True)
class ParseAllSummary:
    """Итог полного прохода по списку источников."""

    news_saved: int
    contacts_saved: int
    elapsed_seconds: float
    sources_total: int
    sources_failed: int

    @property
    def sources_ok(self) -> int:
        return self.sources_total - self.sources_failed


async def parse_all_sources(*, wait_if_busy: bool = False) -> ParseAllSummary:
    """Собрать все настроенные источники и сохранить новости и контакты.

    wait_if_busy: если False и сбор уже идёт — ParseAllSourcesBusyError (бот/планировщик).
    Если True — ждать освобождения (например, одиночный legacy-цикл run_scheduler).
    """

    if not wait_if_busy and _parse_all_lock.locked():
        raise ParseAllSourcesBusyError("parse_all_sources already running")

    async with _parse_all_lock:
        return await _parse_all_sources_impl()


async def _record_source_tick(tick: SourceTickMetrics) -> None:
    try:
        repo = await get_repository()
        await repo.upsert_source_health(
            {
                "source_key": tick.key,
                "source_type": tick.source_type,
                "source_name": tick.source_name,
                "source_url": tick.source_url,
                "ok": tick.ok,
                "latency_ms": tick.latency_ms,
                "collected": tick.collected,
                "parsed": tick.parsed,
                "duplicates": tick.duplicates,
                "rejected": tick.rejected,
                "errors": tick.errors,
                "error": tick.error,
            }
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("source_health upsert failed for %s", tick.source_url)


async def _parse_all_sources_impl() -> ParseAllSummary:
    total_news_saved = 0
    total_contacts_saved = 0
    sources_failed = 0
    sources_all = list(list_sources())
    sources, _chunk_mode = _slice_sources_for_chunk(sources_all)
    t0 = time.perf_counter()

    skip_media = getattr(config, "TELEGRAM_SKIP_MEDIA_FULL_COLLECT", True)
    source_results: list[dict[str, object]] = []
    for source in sources:
        manual_source = ManualSource(
            type=source.type,
            url=source.url,
            name=source.name or source.url,
        )
        tick_t0 = time.perf_counter()
        try:
            items, contacts = await _fetch_items(
                manual_source,
                limit=getattr(config, "MAX_MESSAGES", None),
                download_media=False if manual_source.type == "telegram" and skip_media else True,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - tick_t0) * 1000.0
            sources_failed += 1
            LOGGER.exception("Failed to collect source %s", source.url)
            tick = SourceTickMetrics(
                source_type=manual_source.type,
                source_name=manual_source.name,
                source_url=source.url,
                ok=False,
                latency_ms=latency_ms,
                errors=1,
                error=f"{type(exc).__name__}: {exc}",
            )
            await _record_source_tick(tick)
            source_results.append(tick.to_result_row())
            continue

        collected = len(items)
        stats = await save_news_detailed(items) if items else None
        saved = stats.saved if stats else 0
        duplicates = stats.duplicates if stats else 0
        write_errors = stats.errors if stats else 0
        total_news_saved += saved
        if items:
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
        latency_ms = (time.perf_counter() - tick_t0) * 1000.0
        # Fetch ok; persist errors count as rejected/errors without failing the source tick.
        tick_ok = write_errors == 0
        if not tick_ok:
            sources_failed += 1
        tick = SourceTickMetrics(
            source_type=manual_source.type,
            source_name=manual_source.name,
            source_url=source.url,
            ok=tick_ok,
            latency_ms=latency_ms,
            collected=collected,
            parsed=collected,
            saved=saved,
            duplicates=duplicates,
            rejected=0,
            errors=write_errors,
            error="" if tick_ok else f"persist_errors={write_errors}",
        )
        await _record_source_tick(tick)
        source_results.append(tick.to_result_row())

    if getattr(config, "ENGAGEMENT_COLLECT_ENABLED", False):
        try:
            from services.channel_engagement import run_channel_engagement

            eng = await run_channel_engagement(sync_sheet=True)
            LOGGER.info(
                "engagement after parse saved_db=%s sheet_up=%s",
                eng.saved_db,
                eng.sheet_updated,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("engagement collect after parse failed")

    elapsed = time.perf_counter() - t0
    LOGGER.info(
        "Collection finished: news_saved=%s contacts_saved=%s elapsed=%.1fs failed_sources=%s/%s (tick_sources=%s all_sources=%s)",
        total_news_saved,
        total_contacts_saved,
        elapsed,
        sources_failed,
        len(sources),
        len(sources),
        len(sources_all),
    )
    save_collect_report(
        sources_total=len(sources),
        sources_failed=sources_failed,
        news_saved=total_news_saved,
        contacts_saved=total_contacts_saved,
        elapsed_seconds=elapsed,
        results=source_results,
    )
    return ParseAllSummary(
        news_saved=total_news_saved,
        contacts_saved=total_contacts_saved,
        elapsed_seconds=elapsed,
        sources_total=len(sources),
        sources_failed=sources_failed,
    )


def run_scheduler(interval_hours: float | None = None) -> None:
    """Legacy helper to execute collection on a simple interval."""

    interval = interval_hours or max(float(config.PARSING_INTERVAL) / 3600, 0.1)

    async def _runner() -> None:
        while True:
            await parse_all_sources(wait_if_busy=True)
            await asyncio.sleep(interval * 3600)

    asyncio.run(_runner())


__all__ = [
    "ParseAllSummary",
    "ParseAllSourcesBusyError",
    "parse_all_sources",
    "parse_all_sources_busy",
    "run_scheduler",
]
