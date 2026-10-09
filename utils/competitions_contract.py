"""Контракт листа competitions_ticker (синхрон с Site_MyWave COMPETITIONS_TICKER_CONTRACT_v1)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

# Обязательные колонки (строка 1 листа, без merge)
COMPETITIONS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "status",
    "discipline",
    "event_name",
    "location",
    "country",
    "start_date",
    "end_date",
    "event_url",
    "source_name",
    "source_url",
    "updated_at",
)

# Рекомендуемые parser-side
COMPETITIONS_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "ingest_status",
    "ingest_error",
    "checksum",
    "raw_title",
    "ticker_text",
    "is_live",
    "display_phase",
)

COMPETITIONS_COLUMNS: tuple[str, ...] = COMPETITIONS_REQUIRED_COLUMNS + COMPETITIONS_OPTIONAL_COLUMNS

VALID_STATUSES = frozenset({"ACTIVE", "DRAFT", "ARCHIVED"})
VALID_DISCIPLINES = frozenset({"wakesurf", "wakeboard", "both"})

# Строки приёмки Site/Parser — не показывать на prod после теста
ACCEPTANCE_TEST_IDS: frozenset[str] = frozenset({"test-1", "test-2", "test-3"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_status(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in VALID_STATUSES else "DRAFT"


def normalize_discipline(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in VALID_DISCIPLINES else "both"


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_valid_http_url(value: object) -> bool:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    return bool(urlparse(text).netloc)


def _normalized_http_url(value: object) -> str:
    text = str(value or "").strip()
    if not is_valid_http_url(text):
        return ""
    # Явные плейсхолдеры не должны попадать в prod/source_url.
    if "REAL-SOURCE-URL-HERE" in text.upper():
        return ""
    return text


def competition_display_phase(
    row: Mapping[str, Any],
    *,
    today: date | None = None,
) -> str:
    """Фаза отображения: upcoming | live | past (UTC date)."""
    ref_today = today or datetime.now(timezone.utc).date()
    start = _parse_date(row.get("start_date"))
    end = _parse_date(row.get("end_date"))
    if not start or not end:
        return "upcoming"
    if end < ref_today:
        return "past"
    if start <= ref_today <= end:
        return "live"
    return "upcoming"


def competition_is_live(row: Mapping[str, Any], *, today: date | None = None) -> bool:
    """Событие идёт прямо сейчас: ACTIVE и today между start_date и end_date."""
    if normalize_status(row.get("status")) != "ACTIVE":
        return False
    return competition_display_phase(row, today=today) == "live"


def validate_competition_row(row: Mapping[str, Any]) -> tuple[bool, str]:
    """Минимальная валидация перед записью в лист."""
    comp_id = str(row.get("id") or "").strip()
    if not comp_id:
        return False, "missing_id"
    status = normalize_status(row.get("status"))
    if status not in VALID_STATUSES:
        return False, "invalid_status"
    discipline = normalize_discipline(row.get("discipline"))
    if discipline not in VALID_DISCIPLINES:
        return False, "invalid_discipline"
    if not str(row.get("event_name") or "").strip():
        return False, "missing_event_name"
    start = _parse_date(row.get("start_date"))
    end = _parse_date(row.get("end_date"))
    if not start:
        return False, "invalid_start_date"
    if not end:
        return False, "invalid_end_date"
    if end < start:
        return False, "end_before_start"
    return True, ""


def normalize_competition_row(row: Mapping[str, Any], *, now_iso: str | None = None) -> dict[str, Any]:
    """Приводит строку к контракту перед upsert."""
    now = now_iso or utc_now_iso()
    start = _parse_date(row.get("start_date"))
    end = _parse_date(row.get("end_date"))
    if start and not end:
        end = start
    event_url = _normalized_http_url(row.get("event_url"))
    source_url = _normalized_http_url(row.get("source_url"))
    if not source_url and event_url:
        source_url = event_url
    if not event_url and source_url:
        event_url = source_url
    out: dict[str, Any] = {
        "id": str(row.get("id") or "").strip(),
        "status": normalize_status(row.get("status")),
        "discipline": normalize_discipline(row.get("discipline")),
        "event_name": str(row.get("event_name") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "country": str(row.get("country") or "").strip(),
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "event_url": event_url,
        "source_name": str(row.get("source_name") or "").strip(),
        "source_url": source_url,
        "updated_at": str(row.get("updated_at") or "").strip() or now,
        "ingest_status": str(row.get("ingest_status") or "parsed").strip(),
        "ingest_error": str(row.get("ingest_error") or "").strip(),
        "checksum": str(row.get("checksum") or "").strip(),
        "raw_title": str(row.get("raw_title") or row.get("event_name") or "").strip(),
        "ticker_text": str(row.get("ticker_text") or "").strip(),
    }
    if not out["checksum"]:
        base = "|".join(
            [
                out["id"],
                out["event_name"],
                out["start_date"],
                out["end_date"],
                out["source_url"],
            ]
        )
        import hashlib

        out["checksum"] = hashlib.sha256(base.encode("utf-8")).hexdigest()
    if not out["ticker_text"]:
        out["ticker_text"] = build_ticker_text(out)
    phase = competition_display_phase(out)
    out["display_phase"] = phase
    out["is_live"] = "true" if competition_is_live(out) else "false"
    return out


def _discipline_label(discipline: str) -> str:
    mapping = {
        "wakesurf": "Wakesurf",
        "wakeboard": "Wakeboard",
        "both": "Wakesurf & Wakeboard",
    }
    return mapping.get(discipline, discipline.capitalize())


def _format_date_range(start: date, end: date) -> str:
    if start == end:
        return f"{start.day:02d}.{start.month:02d}.{start.year}"
    if start.year == end.year:
        return f"{start.day:02d}.{start.month:02d}–{end.day:02d}.{end.month:02d}.{end.year}"
    return f"{start.day:02d}.{start.month:02d}.{start.year}–{end.day:02d}.{end.month:02d}.{end.year}"


def build_ticker_text(row: Mapping[str, Any]) -> str:
    """Авто-текст marquee, если ticker_text пуст (как на сайте)."""
    discipline = normalize_discipline(row.get("discipline"))
    event_name = str(row.get("event_name") or "").strip()
    location = str(row.get("location") or "").strip()
    country = str(row.get("country") or "").strip()
    start = _parse_date(row.get("start_date"))
    end = _parse_date(row.get("end_date"))
    place_parts = [p for p in (location, country) if p]
    place = ", ".join(place_parts) if place_parts else "—"
    dates = _format_date_range(start, end) if start and end else "—"
    return f"{_discipline_label(discipline)} · {event_name} · {place} · {dates}"


def should_archive_row(row: Mapping[str, Any], *, today: date | None = None) -> bool:
    """После end_date переводим в ARCHIVED (операционное правило контракта)."""
    today = today or datetime.now(timezone.utc).date()
    end = _parse_date(row.get("end_date"))
    if not end:
        return False
    if end < today:
        return True
    status = normalize_status(row.get("status"))
    return status == "ARCHIVED"


def acceptance_test_rows(*, today: date | None = None) -> list[dict[str, Any]]:
    """Три строки приёмки из brief Site_MyWave."""
    from datetime import timedelta

    today = today or datetime.now(timezone.utc).date()
    fs = (today + timedelta(days=30)).isoformat()
    fe = (today + timedelta(days=33)).isoformat()
    past_end = (today - timedelta(days=10)).isoformat()
    past_start = (today - timedelta(days=15)).isoformat()
    now = utc_now_iso()
    return [
        normalize_competition_row(
            {
                "id": "test-1",
                "status": "ACTIVE",
                "discipline": "wakesurf",
                "event_name": "Parser Acceptance — Future Event A",
                "location": "Orlando",
                "country": "USA",
                "start_date": fs,
                "end_date": fe,
                "event_url": "https://example.com/events/test-1",
                "source_name": "parser_acceptance",
                "source_url": "https://example.com/sources/parser",
                "updated_at": now,
            }
        ),
        normalize_competition_row(
            {
                "id": "test-2",
                "status": "ACTIVE",
                "discipline": "wakeboard",
                "event_name": "Parser Acceptance — Future Event B",
                "location": "Geneva",
                "country": "Switzerland",
                "start_date": fs,
                "end_date": fe,
                "event_url": "https://example.com/events/test-2",
                "source_name": "parser_acceptance",
                "source_url": "https://example.com/sources/parser",
                "updated_at": now,
            }
        ),
        normalize_competition_row(
            {
                "id": "test-3",
                "status": "ARCHIVED",
                "discipline": "both",
                "event_name": "Parser Acceptance — Past Event",
                "location": "Moscow",
                "country": "Russia",
                "start_date": past_start,
                "end_date": past_end,
                "event_url": "https://example.com/events/test-3",
                "source_name": "parser_acceptance",
                "source_url": "https://example.com/sources/parser",
                "updated_at": now,
            }
        ),
    ]


def filter_competition_rows_for_window(
    rows: Iterable[Mapping[str, Any]],
    *,
    max_end_date: date,
    min_end_date: date | None = None,
    drop_past: bool = True,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Оставить события в окне дат: только upcoming/live (end_date >= today)."""
    ref_today = today or datetime.now(timezone.utc).date()
    floor = min_end_date or ref_today
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        normalized = normalize_competition_row(raw)
        ok, _reason = validate_competition_row(normalized)
        if not ok:
            continue
        end = _parse_date(normalized.get("end_date"))
        start = _parse_date(normalized.get("start_date"))
        if not end or not start:
            continue
        if end > max_end_date:
            continue
        if drop_past and end < floor:
            continue
        comp_id = str(normalized.get("id") or "").strip()
        if not comp_id or comp_id in seen:
            continue
        seen.add(comp_id)
        row = normalize_competition_row(
            {
                **normalized,
                "status": "ARCHIVED" if end < ref_today else "ACTIVE",
            }
        )
        out.append(row)
    return out


__all__ = [
    "ACCEPTANCE_TEST_IDS",
    "COMPETITIONS_COLUMNS",
    "COMPETITIONS_REQUIRED_COLUMNS",
    "VALID_DISCIPLINES",
    "VALID_STATUSES",
    "acceptance_test_rows",
    "build_ticker_text",
    "competition_display_phase",
    "competition_is_live",
    "filter_competition_rows_for_window",
    "normalize_competition_row",
    "should_archive_row",
    "utc_now_iso",
    "validate_competition_row",
]
