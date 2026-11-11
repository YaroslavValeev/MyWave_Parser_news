"""Авто-выделение competition candidates из обычных ingest-новостей."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from config.settings import config
from nlp.sanitize import sanitize_text
from services.competitions_ticker_sync import upsert_competition_rows
from utils.competitions_contract import is_valid_http_url, normalize_competition_row

LOGGER = logging.getLogger(__name__)

_TARGET_WAKESURF_MARKERS = (
    "wakesurf",
    "wakesurfing",
    "вейксерф",
    "вейксерфинг",
)
_TARGET_WAKEBOARD_MARKERS = (
    "wakeboard",
    "wakeboarding",
    "вейкборд",
    "вейкбординг",
)
_TARGET_GENERIC_WAKE_MARKERS = (
    " wake ",
    "вейк ",
    "вейк-",
    "wake ",
    "wake-",
)
_TARGET_WAKESURF_SOURCE_MARKERS = (
    "wakesurf",
    "wsws",
)
_TARGET_WAKEBOARD_SOURCE_MARKERS = (
    "wakeboardingmag",
    "wakeboarding magazine",
    "wakeboard magazine",
    "thewwa",
    "world wake association",
    "alliancewake",
    "alliance wake",
    "unleashedwake",
    "unleashed wake",
    "makeawakemarine",
    "make a wake",
    "russian_waterski",
    "russian waterski",
    "ruwf",
    "фвлс",
    "waterskifed",
)
_TRUSTED_FEDERATION_SOURCE_MARKERS = (
    "russian_waterski",
    "russian waterski",
    "ruwf",
    "фвлс",
    "waterskifed",
    "воднолыжн",
)
_BULLET_PREFIX_RE = re.compile(r"^[\s•●▪*\-–—]+")
_NON_TARGET_MARKERS = (
    "доска с веслом",
    "sup ",
    " sup",
    "sup-race",
    "paddle",
    "surf ",
    " surfing",
    "серфинг",
    "кайт",
    "kitesurf",
    "скейт",
    "skate",
)
_EVENT_MARKERS = (
    "championship",
    "competition",
    "contest",
    "cup",
    "open",
    "classic",
    "tour",
    "tournament",
    "соревн",
    "чемпионат",
    "кубок",
    "первенств",
    "этап",
    "регистрац",
    "registration",
)
_ANNOUNCEMENT_MARKERS = (
    "join us",
    "sign up",
    "registration opens",
    "registration is open",
    "entries open",
    "open for entries",
    "приглашаем",
    "открыта регистрация",
    "стартовала регистрация",
    "анонс",
)
_RESULT_MARKERS = (
    "результат",
    "results",
    "итоги",
    "победител",
    "поздравляем",
)
_OPINION_MARKERS = (
    "запретами",
    "бойкотами",
    "показываете",
    "лицемерие",
    "смотрите видео",
    "как это было в прошлом году",
)
_GENERIC_CALENDAR_MARKERS = (
    "календарь спортивных мероприятий",
    "calendar of events",
    "актуальную информацию о спортивных соревнованиях",
)
_LOCATION_PREFIX_RE = re.compile(
    r"(?:^|[\s:>])(?:локация|location|venue|место)\s*[:\-]\s*(?P<loc>[^.\n]{2,100})",
    re.IGNORECASE,
)
_LOCATION_EMOJI_RE = re.compile(r"📍\s*(?P<loc>[^\n]{2,100})")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_NUMERIC_DATE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})(?:\.(?P<y1>20\d{2}))?"
    r"(?:\s*[–—-]\s*(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})(?:\.(?P<y2>20\d{2}))?)?\b"
)
_TEXTUAL_SAME_MONTH_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\s*(?:[–—-]\s*(?P<d2>\d{1,2}))?\s+"
    r"(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
_TEXTUAL_CROSS_MONTH_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\s+(?P<m1>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s*[–—-]\s*(?P<d2>\d{1,2})\s+(?P<m2>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_TRANSLIT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def extract_competition_row_from_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Построить строку competitions_ticker из одного ingest item."""
    clickable_source_url = _resolve_clickable_source_url(item)
    if not is_valid_http_url(clickable_source_url):
        return None

    title = _pick_text(item, "raw_title", "title")
    content = _pick_text(item, "raw_content", "content", "transcript")
    raw_tags = _pick_text(item, "raw_tags")
    source_name = _pick_text(item, "source_name", "source")
    source_type = _resolve_source_type(item)
    source_origin_url = _pick_text(item, "source_url")
    combined = "\n".join(part for part in (title, content, raw_tags) if part).strip()
    if not combined:
        return None

    discipline = _detect_discipline(
        combined,
        source_name=source_name,
        source_type=source_type,
        source_url=source_origin_url,
    )
    if discipline is None:
        return None
    if _contains_any(combined.lower(), _RESULT_MARKERS):
        return None
    if _contains_any(combined.lower(), _OPINION_MARKERS):
        return None
    if not _looks_like_event(combined, source_type=source_type):
        return None

    event_name = _extract_event_name(title=title, content=content, source_name=source_name)
    if not event_name or not _is_valid_event_name(event_name):
        return None

    start_date, end_date = _extract_date_range(
        combined,
        published_at=item.get("original_published_at") or item.get("date") or item.get("created_at"),
    )
    if not start_date:
        return None

    location = _extract_location(content)
    country = _guess_country(location=location, text=combined)
    status = "ARCHIVED" if date.fromisoformat(end_date or start_date) < datetime.now(timezone.utc).date() else "ACTIVE"
    row = normalize_competition_row(
        {
            "id": _make_competition_id(item, event_name, start_date),
            "status": status,
            "discipline": discipline,
            "event_name": event_name,
            "location": location,
            "country": country,
            "start_date": start_date,
            "end_date": end_date or start_date,
            "event_url": clickable_source_url,
            "source_name": source_name or _derive_source_slug(item),
            "source_url": clickable_source_url,
            "raw_title": title or event_name,
            "ingest_status": "parsed_from_news",
            "ingest_error": "",
        }
    )
    return row


def extract_competition_rows_from_item(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Извлечь 0..N строк competitions_ticker из одного ingest item."""
    calendar_rows = _extract_federation_calendar_rows(item)
    if calendar_rows:
        return calendar_rows
    single = extract_competition_row_from_item(item)
    return [single] if single else []


def extract_competition_rows_from_news(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        for row in extract_competition_rows_from_item(item):
            comp_id = str(row.get("id") or "").strip()
            if not comp_id or comp_id in seen_ids:
                continue
            seen_ids.add(comp_id)
            rows.append(row)
    return rows


async def sync_news_competitions(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Выделить competition candidates из новостей и upsert в competitions_ticker."""
    if not getattr(config, "COMPETITIONS_SYNC_ENABLED", True):
        return {"input": 0, "upserted": 0, "updated": 0, "appended": 0, "skipped": 0, "errors": 0}

    rows = extract_competition_rows_from_news(items)
    if not rows:
        return {"input": 0, "upserted": 0, "updated": 0, "appended": 0, "skipped": 0, "errors": 0}
    stats = await upsert_competition_rows(rows, invalidate_cache=True)
    LOGGER.info("news_competitions sync stats=%s", stats)
    return stats


def _pick_text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if key in {"raw_title", "title"}:
            text = _normalize_line(str(value))
        else:
            text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_clickable_source_url(item: Mapping[str, Any]) -> str:
    for key in ("link", "source_url", "canonical_url"):
        value = str(item.get(key) or "").strip()
        if is_valid_http_url(value):
            return value
    return ""


def _resolve_source_type(item: Mapping[str, Any]) -> str:
    source_type = str(item.get("source_type") or "").strip().lower()
    if source_type:
        return source_type
    source = str(item.get("source") or "").strip()
    if ":" in source:
        return source.split(":", 1)[0].strip().lower()
    return ""


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    lowered = f" {text.lower()} "
    return any(marker in lowered for marker in markers)


def _detect_discipline(text: str, *, source_name: str, source_type: str = "", source_url: str = "") -> str | None:
    lowered = f" {text.lower()} {source_name.lower()} "
    # Сначала целевые дисциплины: иначе «surf » ложно срабатывает внутри «wakesurf».
    if _contains_any(lowered, _TARGET_WAKESURF_MARKERS):
        return "wakesurf"
    if _contains_any(lowered, _TARGET_WAKEBOARD_MARKERS):
        return "wakeboard"
    if _contains_any(lowered, _NON_TARGET_MARKERS):
        return None
    if source_type in {"rss", "website"}:
        trusted_source = f" {source_name.lower()} {source_url.lower()} "
        if _contains_any(trusted_source, _TARGET_WAKESURF_SOURCE_MARKERS):
            return "wakesurf"
        if _contains_any(trusted_source, _TARGET_WAKEBOARD_SOURCE_MARKERS):
            return "wakeboard"
    return None


def _is_trusted_federation_source(item: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("source", "source_name", "source_url", "link")
    ).lower()
    return _contains_any(haystack, _TRUSTED_FEDERATION_SOURCE_MARKERS)


def _is_bullet_line(line: str) -> bool:
    stripped = str(line or "").lstrip()
    return bool(stripped) and stripped[0] in "•●▪*-"


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX_RE.sub("", str(line or ""), count=1).strip()


def _detect_section_discipline(line: str) -> str | None:
    lowered = str(line or "").strip().lower()
    if not lowered or _is_bullet_line(line):
        return None
    if "вейкборд-катер" in lowered or "wakeboard" in lowered:
        return "wakeboard"
    if _contains_any(lowered, _TARGET_WAKESURF_MARKERS):
        return "wakesurf"
    if _contains_any(lowered, _TARGET_WAKEBOARD_MARKERS):
        return "wakeboard"
    return None


def _extract_location_from_calendar_bullet(text: str) -> str:
    cleaned = str(text or "").strip()
    for sep in (" – ", " — ", " - "):
        if sep not in cleaned:
            continue
        left, right = cleaned.rsplit(sep, 1)
        if _extract_date_range(left, published_at=None)[0]:
            location = _normalize_line(right)
            if location:
                return location[:120]
    return ""


def _looks_like_calendar_event_name(line: str) -> bool:
    text = _normalize_line(line)
    if not text or len(text) < 4:
        return False
    lowered = text.lower()
    if "календар" in lowered and not _contains_any(lowered, _EVENT_MARKERS):
        return False
    return _contains_any(lowered, _EVENT_MARKERS)


def _build_calendar_competition_row(
    item: Mapping[str, Any],
    *,
    event_name: str,
    discipline: str,
    date_text: str,
    location: str,
    combined_text: str,
) -> dict[str, Any] | None:
    clickable_source_url = _resolve_clickable_source_url(item)
    if not is_valid_http_url(clickable_source_url):
        return None
    title = _pick_text(item, "raw_title", "title")
    source_name = _pick_text(item, "source_name", "source")
    start_date, end_date = _extract_date_range(
        date_text,
        published_at=item.get("original_published_at") or item.get("date") or item.get("created_at"),
    )
    if not start_date or not _is_valid_event_name(event_name):
        return None
    country = _guess_country(location=location, text=location or combined_text)
    status = (
        "ARCHIVED"
        if date.fromisoformat(end_date or start_date) < datetime.now(timezone.utc).date()
        else "ACTIVE"
    )
    return normalize_competition_row(
        {
            "id": _make_competition_id(item, event_name, start_date),
            "status": status,
            "discipline": discipline,
            "event_name": event_name,
            "location": location,
            "country": country,
            "start_date": start_date,
            "end_date": end_date or start_date,
            "event_url": clickable_source_url,
            "source_name": source_name or _derive_source_slug(item),
            "source_url": clickable_source_url,
            "raw_title": title or event_name,
            "ingest_status": "parsed_from_federation_calendar",
            "ingest_error": "",
        }
    )


def _extract_federation_calendar_rows(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Разобрать посты ФВЛС/RUWF с несколькими событиями в bullet-списке."""
    if not _is_trusted_federation_source(item):
        return []

    title = _pick_text(item, "raw_title", "title")
    content = _pick_text(item, "raw_content", "content", "transcript")
    combined = "\n".join(part for part in (title, content) if part).strip()
    if not combined:
        return []

    bullet_lines = [line for line in content.splitlines() if _is_bullet_line(line)]
    if len(bullet_lines) < 2 and "календар" not in combined.lower():
        return []

    default_discipline = _detect_discipline(
        combined,
        source_name=_pick_text(item, "source_name", "source"),
        source_type=_resolve_source_type(item),
        source_url=_pick_text(item, "source_url"),
    ) or "wakeboard"

    rows: list[dict[str, Any]] = []
    current_discipline = default_discipline
    pending_name = ""

    for raw_line in content.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue

        section_discipline = _detect_section_discipline(line)
        if section_discipline:
            current_discipline = section_discipline
            pending_name = ""
            continue

        if _is_bullet_line(line):
            bullet = _strip_bullet(line)
            start_date, _end_date = _extract_date_range(
                bullet,
                published_at=item.get("original_published_at") or item.get("date") or item.get("created_at"),
            )
            if start_date:
                event_name = pending_name or _extract_event_name(
                    title="",
                    content=bullet,
                    source_name=_pick_text(item, "source_name", "source"),
                )
                location = _extract_location_from_calendar_bullet(bullet)
                row = _build_calendar_competition_row(
                    item,
                    event_name=event_name,
                    discipline=current_discipline,
                    date_text=bullet,
                    location=location,
                    combined_text=combined,
                )
                if row:
                    rows.append(row)
                pending_name = ""
                continue
            if _looks_like_calendar_event_name(bullet):
                pending_name = _normalize_line(bullet)
            continue

        if _looks_like_calendar_event_name(line):
            pending_name = _normalize_line(line)

    if len(rows) < 2:
        return []
    return rows


def _looks_like_event(text: str, *, source_type: str = "") -> bool:
    lowered = text.lower()
    if "календар" in lowered:
        return False
    if _contains_any(lowered, _GENERIC_CALENDAR_MARKERS):
        return False
    if _contains_any(lowered, _EVENT_MARKERS):
        return True
    # Для RSS допускаем более мягкий сигнал: announcement + дата.
    if source_type == "rss" and _contains_any(lowered, _ANNOUNCEMENT_MARKERS):
        return _has_date_hint(text)
    return False


def _has_date_hint(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (_TEXTUAL_CROSS_MONTH_RE, _TEXTUAL_SAME_MONTH_RE, _NUMERIC_DATE_RE)
    )


def _is_valid_event_name(name: str) -> bool:
    text = str(name or "").strip()
    if len(text) < 6 or len(text) > 160:
        return False
    if text.endswith("…") or text.endswith("..."):
        return False
    if "http://" in text.lower() or "https://" in text.lower() or "](" in text:
        return False
    lowered = text.lower()
    if not _contains_any(lowered, _EVENT_MARKERS):
        return False
    if _contains_any(lowered, _OPINION_MARKERS + _RESULT_MARKERS):
        return False
    return True


def _extract_event_name(*, title: str, content: str, source_name: str) -> str:
    candidates: list[tuple[int, str]] = []
    for raw in [title, *str(content or "").splitlines()]:
        line = _normalize_line(raw)
        if len(line) < 4 or len(line) > 160:
            continue
        score = _score_event_name_candidate(line, source_name=source_name)
        if score <= 0:
            continue
        candidates.append((score, line))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    return candidates[0][1][:200].strip()


def _score_event_name_candidate(line: str, *, source_name: str) -> int:
    lowered = line.lower()
    if not _contains_any(lowered, _EVENT_MARKERS):
        return 0
    score = 0
    if _contains_any(lowered, _TARGET_WAKESURF_MARKERS + _TARGET_WAKEBOARD_MARKERS):
        score += 6
    if _contains_any(lowered, _EVENT_MARKERS):
        score += 4
    if _contains_any(lowered, _RESULT_MARKERS):
        score -= 10
    if "календар" in lowered:
        score -= 4
    if _contains_any(f" {lowered} ", _TARGET_GENERIC_WAKE_MARKERS):
        score += 2
    if _contains_any(source_name.lower(), _TARGET_WAKESURF_MARKERS + _TARGET_WAKEBOARD_MARKERS):
        score += 1
    return score


def _normalize_line(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[^\wА-Яа-яЁё]+", "", text)
    return sanitize_text(text)


def _extract_date_range(text: str, *, published_at: object) -> tuple[str | None, str | None]:
    anchor_year = _infer_anchor_year(text, published_at)

    cross = _TEXTUAL_CROSS_MONTH_RE.search(text)
    if cross:
        return _build_range(
            int(cross.group("d1")),
            _month_number(cross.group("m1")),
            int(cross.group("d2")),
            _month_number(cross.group("m2")),
            int(cross.group("year") or anchor_year),
        )

    same = _TEXTUAL_SAME_MONTH_RE.search(text)
    if same:
        start_day = int(same.group("d1"))
        end_day = int(same.group("d2") or start_day)
        month = _month_number(same.group("month"))
        return _build_range(start_day, month, end_day, month, int(same.group("year") or anchor_year))

    numeric = _NUMERIC_DATE_RE.search(text)
    if numeric:
        start_day = int(numeric.group("d1"))
        start_month = int(numeric.group("m1"))
        start_year = int(numeric.group("y1") or numeric.group("y2") or anchor_year)
        end_day = int(numeric.group("d2") or start_day)
        end_month = int(numeric.group("m2") or start_month)
        end_year = int(numeric.group("y2") or numeric.group("y1") or start_year)
        return _build_range(start_day, start_month, end_day, end_month, start_year, end_year=end_year)

    return None, None


def _infer_anchor_year(text: str, published_at: object) -> int:
    match = _YEAR_RE.search(text)
    if match:
        return int(match.group(1))
    parsed = _parse_datetime(published_at)
    if parsed is not None:
        return parsed.year
    return datetime.now(timezone.utc).year


def _month_number(raw: str | None) -> int:
    return _MONTHS[str(raw or "").strip().lower()]


def _build_range(
    start_day: int,
    start_month: int,
    end_day: int,
    end_month: int,
    start_year: int,
    *,
    end_year: int | None = None,
) -> tuple[str | None, str | None]:
    try:
        start = date(start_year, start_month, start_day)
        end = date(end_year or start_year, end_month, end_day)
    except ValueError:
        return None, None
    return start.isoformat(), end.isoformat()


def _extract_location(content: str) -> str:
    for pattern in (_LOCATION_PREFIX_RE, _LOCATION_EMOJI_RE):
        match = pattern.search(content)
        if match:
            return _normalize_line(match.group("loc"))[:120]
    return ""


def _guess_country(*, location: str, text: str) -> str:
    lowered = f"{location} {text}".lower()
    if any(marker in lowered for marker in ("cyprus", "кипр", "айя-напа", "konnos")):
        return "Cyprus"
    if any(
        marker in lowered
        for marker in (
            "russia",
            "россия",
            "москов",
            "сочи",
            "краснодар",
            "санкт",
            "казань",
            "татарстан",
            "свердлов",
            "сысерт",
            "сербор",
        )
    ):
        return "Russia"
    if any(marker in lowered for marker in ("serbia", "сербия", "belgrade", "белград")):
        return "Serbia"
    if any(marker in lowered for marker in ("italy", "италия", "italia")):
        return "Italy"
    return ""


def _make_competition_id(item: Mapping[str, Any], event_name: str, start_date: str) -> str:
    source_slug = _derive_source_slug(item)
    event_slug = _slugify(event_name)[:72]
    if source_slug and event_slug:
        return f"{source_slug}-{event_slug}-{start_date}"
    digest = hashlib.sha256(f"{source_slug}|{event_name}|{start_date}".encode("utf-8")).hexdigest()[:16]
    return f"news-{digest}"


def _derive_source_slug(item: Mapping[str, Any]) -> str:
    for key in ("source_url", "link"):
        raw = str(item.get(key) or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if host.endswith("t.me") or host.endswith("telegram.me"):
            if path_parts:
                if path_parts[0] == "c" and len(path_parts) > 1:
                    return _slugify(path_parts[1])
                return _slugify(path_parts[0])
        host = host.removeprefix("www.")
        if host:
            return _slugify(host.split(":")[0])
    return _slugify(str(item.get("source_name") or item.get("source") or "news"))


def _slugify(value: str) -> str:
    lowered = str(value or "").lower().translate(_TRANSLIT)
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-")


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[: len(fmt)], fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "extract_competition_row_from_item",
    "extract_competition_rows_from_item",
    "extract_competition_rows_from_news",
    "sync_news_competitions",
]
