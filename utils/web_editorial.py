"""Подсказки для карточки сайта (raw_feed), отдельно от Telegram."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping

from utils.telegram_editorial import hints_from_item

WEB_NEWS_CARD_MAX = 1800
WEB_ARTICLE_MIN = 3000
WEB_ARTICLE_MAX = 18000
WEB_LEAD_MAX = 300
WEB_H2_EVERY_CHARS = 500
WEB_MEDIA_EVERY_WORDS = 350

_HEADING = re.compile(r"(?m)^#{2,3}\s+\S+")
_WORDS = re.compile(r"[A-Za-zА-Яа-яЁё0-9\-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class WebEditorialHints:
    chars: int
    lead_chars: int
    headings: int
    words: int
    mode: str  # news_card | article | too_long
    need_h2: bool
    need_media_breaks: bool


def analyze_web_body(*, title: str = "", body: str = "", lead: str = "") -> WebEditorialHints:
    title = (title or "").strip()
    body = (body or "").strip()
    lead = (lead or "").strip()
    full = "\n\n".join(p for p in (title, body) if p)
    chars = len(full)
    words = len(_WORDS.findall(full))
    headings = len(_HEADING.findall(body))
    if chars > WEB_ARTICLE_MAX:
        mode = "too_long"
    elif chars >= WEB_ARTICLE_MIN:
        mode = "article"
    else:
        mode = "news_card"
    expected_h2 = max(0, chars // WEB_H2_EVERY_CHARS - 1) if mode == "article" else 0
    need_h2 = mode == "article" and headings < expected_h2
    need_media = mode == "article" and words >= WEB_MEDIA_EVERY_WORDS * 2
    return WebEditorialHints(
        chars=chars,
        lead_chars=len(lead),
        headings=headings,
        words=words,
        mode=mode,
        need_h2=need_h2,
        need_media_breaks=need_media,
    )


def format_web_editorial_html(hints: WebEditorialHints) -> str:
    mode_ru = {
        "news_card": "карточка новости (для витрины ок; лонгрид 3000–18000 не требуется)",
        "article": "зона статьи (3000–18000)",
        "too_long": "длиннее 18000 — лучше разбить",
    }[hints.mode]
    lines = [
        "\n\n<b>Формат сайт</b>",
        f"\nЗнаков: <b>{hints.chars}</b> · {html.escape(mode_ru)}",
        f"\nЛид: {hints.lead_chars} (мягкий ориентир до {WEB_LEAD_MAX})",
    ]
    if hints.lead_chars > WEB_LEAD_MAX:
        lines.append(" — укоротите лид для карточки.")
    if hints.mode == "article":
        lines.append(f"\nПодзаголовков H2/H3 в markdown: {hints.headings}")
    if hints.need_h2:
        lines.append(
            f"\n⚠️ Для лонгрида желательны H2/H3 примерно каждые {WEB_H2_EVERY_CHARS} знаков."
        )
    if hints.need_media_breaks:
        lines.append(
            f"\n⚠️ ~{hints.words} слов: на сайте вставьте фото/цитату каждые ~{WEB_MEDIA_EVERY_WORDS} слов."
        )
    if hints.mode == "too_long":
        lines.append(f"\n⚠️ Больше {WEB_ARTICLE_MAX} знаков — разбейте материал.")
    return "".join(lines)


def web_html_from_item(item: Mapping[str, Any], nlp: Mapping[str, Any] | None = None) -> str:
    from utils.card_preview_text import lead_from_text

    tg = hints_from_item(item, nlp or {})
    nlp = nlp or {}
    title = str(item.get("title") or "").strip()
    if str(nlp.get("summary") or "").strip() and str(nlp.get("author_notes") or "").strip():
        body = f"{nlp.get('summary')}\n\n{nlp.get('author_notes')}"
    else:
        body = str(nlp.get("merged_text") or nlp.get("summary") or item.get("content") or "")
    lead = lead_from_text(body)
    return format_web_editorial_html(analyze_web_body(title=title, body=str(body), lead=lead))


__all__ = [
    "WebEditorialHints",
    "analyze_web_body",
    "format_web_editorial_html",
    "web_html_from_item",
]
