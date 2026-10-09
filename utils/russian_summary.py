from __future__ import annotations

import re
from typing import Any, Mapping

from utils.card_preview_text import to_card_preview_text

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)
_TITLE_PLACEHOLDERS = {"без заголовка", "(без заголовка)"}
_GENERIC_FALLBACK_TOPICS = (
    "вейкбординге",
    "вейксёрфинге",
    "водных лыжах",
    "катерах и индустрии водного спорта",
    "соревнованиях и событиях",
)
_REVIEW_MARKERS = (
    "we test",
    "review",
    "tested",
    "hands-on",
    "first look",
    "gear test",
    "gear review",
    "test:",
)
_PRODUCT_MARKERS = (
    "headphone",
    "earbud",
    "helmet",
    "vest",
    "board",
    "bindings",
    "binding",
    "rope",
    "speaker",
    "goggle",
    "boat",
    "engine",
    "wetsuit",
    "foil",
)
_EVENT_MARKERS = (
    "event",
    "competition",
    "championship",
    "tour",
    "cup",
    "open",
    "соревн",
    "турнир",
    "чемпионат",
    "этап",
    "кубок",
)


def wants_russian(lang: str | None) -> bool:
    value = str(lang or "").strip().lower()
    if not value:
        return True
    return value in {"ru", "rus", "russian", "русский"} or value.startswith("ru-") or "рус" in value


def target_language_label(lang: str | None) -> str:
    value = str(lang or "").strip()
    if wants_russian(value):
        return "русском языке"
    return value or "русском языке"


def has_cyrillic(text: str | None) -> bool:
    return bool(_CYRILLIC_RE.search(str(text or "")))


def is_probably_non_russian(text: str | None) -> bool:
    value = to_card_preview_text(text or "", max_len=1200)
    if not value:
        return False
    cyrillic = len(_CYRILLIC_RE.findall(value))
    latin = len(_LATIN_RE.findall(value))
    if latin < 12:
        return False
    return cyrillic < max(8, latin // 5)


def _item_title(item: Mapping[str, Any], *, max_len: int = 120) -> str:
    for key in ("title", "raw_title", "source"):
        value = to_card_preview_text(str(item.get(key) or ""), max_len=max_len)
        if value and value.lower() not in _TITLE_PLACEHOLDERS:
            return value
    return "Без заголовка"


def _missing_text_context_summary(item: Mapping[str, Any]) -> str:
    title = _item_title(item, max_len=120)
    return to_card_preview_text(
        "В записи нет текстового контента в базе. "
        f"Откройте источник и примите решение вручную. ({title})",
        max_len=260,
    )


def _normalize_title_for_summary(item: Mapping[str, Any]) -> str:
    title = _item_title(item, max_len=180)
    cleaned = title.split("|", 1)[0].strip(" -:|")
    lowered = cleaned.lower()
    for prefix in (
        "we test:",
        "review:",
        "tested:",
        "hands-on:",
        "first look:",
        "gear test:",
        "gear review:",
        "test:",
    ):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip(" -:|")
            break
    return to_card_preview_text(cleaned or title, max_len=180)


def _looks_like_listing_title(title: str) -> bool:
    lowered = str(title or "").lower()
    comma_count = lowered.count(",")
    generic_markers = ("gear", "videos", "tips", "photos", "news", "magazine")
    if comma_count >= 3 and sum(marker in lowered for marker in generic_markers) >= 2:
        return True
    return False


def _looks_generic_russian_summary(summary: str) -> bool:
    text = to_card_preview_text(summary or "", max_len=260)
    if not text:
        return False
    if text.startswith("Англоязычный материал из источника;"):
        return True
    if text.startswith("Материал с видео по водным видам спорта;"):
        return True
    if text.startswith("Материал Wakeboarding Magazine о "):
        return True
    if text.startswith("Материал World Wake Association о "):
        return True
    if not (text.startswith("Материал о ") and text.endswith(".")):
        return False
    body = text[len("Материал о ") : -1]
    if not body:
        return False
    parts = [part.strip() for part in body.split(",") if part.strip()]
    if not parts:
        return False
    return all(part in _GENERIC_FALLBACK_TOPICS for part in parts)


def _build_title_based_summary(item: Mapping[str, Any], source_text: str | None) -> str:
    raw_title = _item_title(item, max_len=180)
    title = _normalize_title_for_summary(item)
    if not title or title.lower() in _TITLE_PLACEHOLDERS:
        return ""
    if _looks_like_listing_title(raw_title):
        return ""
    haystack = f"{raw_title}\n{title}\n{source_text or ''}".lower()
    if any(marker in haystack for marker in _REVIEW_MARKERS) and any(
        marker in haystack for marker in _PRODUCT_MARKERS
    ):
        return (
            f"Обзор {title}. "
            "В материале разбираются характеристики устройства, опыт использования и практические выводы автора."
        )
    if any(marker in haystack for marker in _EVENT_MARKERS):
        return (
            f"Материал о событии «{title}». "
            "В тексте собраны ключевые детали, контекст и важные факты по теме публикации."
        )
    if len(title) >= 18:
        return (
            f"Материал «{title}» с ключевыми деталями и контекстом публикации."
        )
    return ""


def russian_fallback_summary(
    item: Mapping[str, Any] | None,
    source_text: str | None,
    *,
    max_len: int = 260,
) -> str:
    item = item or {}
    text = to_card_preview_text(source_text or "", max_len=1200)
    if not text:
        return _missing_text_context_summary(item)

    title = _normalize_title_for_summary(item)
    haystack = f"{title}\n{text}".lower()
    title_based = _build_title_based_summary(item, text)
    if title_based:
        return to_card_preview_text(title_based, max_len=max_len)

    if "wakeboarding magazine" in haystack:
        summary = "Материал Wakeboarding Magazine о вейкбординге: экипировка, видео, советы, фото, лодки и новости индустрии."
    elif "world wake association" in haystack or "wwa" in haystack:
        summary = "Материал World Wake Association о событиях, соревнованиях и новостях вейкбординга и водных видов спорта."
    elif "mastercraft" in haystack and "rule the water" in haystack:
        summary = "Материал о туре MasterCraft Rule the Water Tour 2026 и возможности посмотреть новые модели лодок на воде."
    elif "youtube" in haystack or "video" in haystack:
        summary = "Материал с видео по водным видам спорта; перед публикацией проверьте источник и выберите главный акцент."
    else:
        topics: list[str] = []
        if any(word in haystack for word in ("wakeboarding", "wakeboard", "вейкборд")):
            topics.append("вейкбординге")
        if any(word in haystack for word in ("wakesurf", "вейксёрф", "вейксерф")):
            topics.append("вейксёрфинге")
        if any(word in haystack for word in ("waterski", "water ski", "водн", "лыж")):
            topics.append("водных лыжах")
        if any(word in haystack for word in ("boat", "boats", "marine", "nautique", "centurion", "катер")):
            topics.append("катерах и индустрии водного спорта")
        if any(word in haystack for word in ("event", "competition", "championship", "tour", "соревн", "турнир")):
            topics.append("соревнованиях и событиях")

        if topics:
            summary = "Материал о " + ", ".join(dict.fromkeys(topics)) + "."
        else:
            summary = "Англоязычный материал из источника; перед публикацией проверьте детали по ссылке и выберите главный акцент."

    return to_card_preview_text(summary, max_len=max_len)


def ensure_russian_summary(
    summary: str | None,
    *,
    item: Mapping[str, Any] | None = None,
    source_text: str | None = None,
    lang: str | None = "ru",
    max_len: int = 260,
) -> str:
    if not wants_russian(lang):
        return to_card_preview_text(summary or "", max_len=max_len)

    normalized = to_card_preview_text(summary or "", max_len=max_len)
    if (
        normalized
        and not is_probably_non_russian(normalized)
        and not _looks_generic_russian_summary(normalized)
    ):
        return normalized

    fallback_source = source_text or summary or ""
    return russian_fallback_summary(item, fallback_source, max_len=max_len)


__all__ = [
    "ensure_russian_summary",
    "has_cyrillic",
    "is_probably_non_russian",
    "russian_fallback_summary",
    "target_language_label",
    "wants_russian",
]
