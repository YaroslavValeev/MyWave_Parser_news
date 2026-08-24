"""Подсказки для Telegram-поста в карточке ревью (не отдельная CMS)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping

# Лента: решение за 2–3 секунды. Зона до «читать далее» в Telegram ~150 знаков.
TELEGRAM_LEAD_CHARS = 150
TELEGRAM_NEWS_MIN = 500
TELEGRAM_NEWS_MAX = 1000
TELEGRAM_EXPERT_MAX = 1800
TELEGRAM_WARN_CHARS = 2000
TELEGRAM_HARD_CHARS = 4096
TELEGRAM_PHOTO_CAPTION_CHARS = 1024
PARAGRAPH_WORD_SOFT = 80

_WATER_START = (
    "в наше время",
    "как известно",
    "сегодня мы хотим",
    "хотелось бы отметить",
    "не секрет что",
    "на сегодняшний день",
    "в данной статье",
    "стоит отметить",
    "безусловно",
    "таким образом",
)
_WATER_PHRASES = (
    "в рамках",
    "осуществить",
    "представляется",
    "целесообразно",
    "имеет место",
    "в целях",
    "на данный момент",
    "в настоящее время",
    "следует подчеркнуть",
    "нельзя не отметить",
    "с целью",
    "является одним из",
)

_WORDS = re.compile(r"[A-Za-zА-Яа-яЁё0-9\-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TelegramEditorialHints:
    chars: int
    lead: str
    lead_ok: bool
    long_paragraphs: int
    water_hits: tuple[str, ...]
    over_warn: bool
    over_hard: bool
    photo_caption_risk: bool
    band: str  # short | news | expert | long


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def analyze_telegram_post(*, title: str = "", body: str = "", has_photo: bool = False) -> TelegramEditorialHints:
    """Оценить будущий Telegram-пост (заголовок + тело без HTML-футера)."""
    title = (title or "").strip()
    body = (body or "").strip()
    full = "\n\n".join(p for p in (title, body) if p)
    chars = len(full)
    lead = _plain(full)[:TELEGRAM_LEAD_CHARS]
    lead_l = lead.lower()
    lead_ok = bool(lead) and not any(lead_l.startswith(w) for w in _WATER_START)
    if lead_ok and len(lead) >= 40:
        # «Вода» в лиде: нет цифр/факта и начинается с канцеляризма внутри окна.
        lead_ok = not any(w in lead_l[:80] for w in _WATER_START)

    long_paragraphs = 0
    for para in re.split(r"\n{2,}", body or title):
        words = _WORDS.findall(para)
        if len(words) > PARAGRAPH_WORD_SOFT:
            long_paragraphs += 1

    hay = _plain(full).lower()
    water = tuple(p for p in _WATER_PHRASES if p in hay)[:6]

    if chars < TELEGRAM_NEWS_MIN:
        band = "short"
    elif chars <= TELEGRAM_NEWS_MAX:
        band = "news"
    elif chars <= TELEGRAM_EXPERT_MAX:
        band = "expert"
    else:
        band = "long"

    return TelegramEditorialHints(
        chars=chars,
        lead=lead,
        lead_ok=lead_ok,
        long_paragraphs=long_paragraphs,
        water_hits=water,
        over_warn=chars > TELEGRAM_WARN_CHARS,
        over_hard=chars > TELEGRAM_HARD_CHARS,
        photo_caption_risk=bool(has_photo) and chars > TELEGRAM_PHOTO_CAPTION_CHARS,
        band=band,
    )


def hints_from_item(item: Mapping[str, Any], nlp: Mapping[str, Any] | None = None) -> TelegramEditorialHints:
    nlp = nlp or {}
    title = str(item.get("title") or "").strip()
    summary = str(nlp.get("summary") or "").strip()
    notes = str(nlp.get("author_notes") or "").strip()
    merged = str(nlp.get("merged_text") or "").strip()
    if summary and notes:
        body = f"{summary}\n\n{notes}"
    elif merged:
        body = merged
    else:
        body = summary or str(item.get("content") or "")
    images = str(item.get("images") or item.get("cover_image_url") or "")
    extra = nlp.get("extra") if isinstance(nlp.get("extra"), Mapping) else {}
    cover = extra.get("cover") if isinstance(extra, Mapping) else None
    has_photo = bool(images.strip()) or (isinstance(cover, Mapping) and cover.get("url"))
    return analyze_telegram_post(title=title, body=body, has_photo=has_photo)


def format_telegram_editorial_html(hints: TelegramEditorialHints) -> str:
    """Короткий блок для карточки ревью (parse_mode=HTML)."""
    band_ru = {
        "short": "короче новости (цель 500–1000)",
        "news": "зона новости (500–1000)",
        "expert": "зона экспертизы (1000–1800)",
        "long": "длиннее экспертизы — лучше ужать",
    }[hints.band]
    lead_mark = "зелёный" if hints.lead_ok else "жёлтый"
    lines = [
        "\n\n<b>Формат Telegram</b>",
        f"\nЗнаков: <b>{hints.chars}</b> · {html.escape(band_ru)}",
        f"\nЛид 150: <i>{html.escape(lead_mark)}</i> — {html.escape(hints.lead[:120] + ('…' if len(hints.lead) > 120 else ''))}",
    ]
    if hints.long_paragraphs:
        lines.append(
            f"\nАбзацев длиннее ~{PARAGRAPH_WORD_SOFT} слов: <b>{hints.long_paragraphs}</b> "
            "(разбейте Enter’ом — правило трёх-четырёх строк)."
        )
    if hints.water_hits:
        shown = ", ".join(hints.water_hits)
        lines.append(f"\nКанцеляризмы: <code>{html.escape(shown)}</code>")
    if hints.photo_caption_risk:
        lines.append(
            f"\n⚠️ С обложкой подпись Telegram ≤ {TELEGRAM_PHOTO_CAPTION_CHARS} знаков — "
            "иначе уйдёт отдельным текстом или обрежется."
        )
    if hints.over_warn:
        lines.append(
            f"\n⚠️ Больше {TELEGRAM_WARN_CHARS} знаков: укоротите или разбейте на 2 сообщения."
        )
    if hints.over_hard:
        lines.append(f"\n⛔ Лимит Telegram {TELEGRAM_HARD_CHARS} знаков — публикация текста упадёт.")
    return "".join(lines)


