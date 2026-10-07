from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from config.settings import config
from nlp.sanitize import sanitize_text
from utils.card_preview_text import to_card_preview_text
from utils.source_context import article_urls, linked_article_context, source_input_hash

_TELEGRAM_HOSTS = {"t.me", "telegram.me"}
_TITLE_PLACEHOLDERS = {"без заголовка", "(без заголовка)"}
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def _normalize_candidate_title(value: object) -> str:
    title = sanitize_text(value)
    if title.lower() in _TITLE_PLACEHOLDERS:
        return ""
    return title


def requires_linked_article_context(item: Mapping[str, Any]) -> bool:
    """An explicitly linked allowed article is the factual source, not its caption."""
    if not getattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", False):
        return False
    urls = article_urls(item)
    return len(urls) == 1 and urlparse(urls[0]).hostname in set(
        getattr(config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ())
    )


def get_item_text_context(item: Mapping[str, Any]) -> str:
    """Вернуть очищенный текстовый контекст материала только из реального контента."""
    evidence = linked_article_context(item)
    if evidence:
        return evidence["text"]
    if requires_linked_article_context(item):
        return ""
    for key in ("content", "transcript"):
        text = sanitize_text(item.get(key))
        if any(char.isalnum() for char in _URL_RE.sub("", text)):
            return text
    return ""


def is_telegram_item(item: Mapping[str, Any]) -> bool:
    link = str(item.get("link") or item.get("source_url") or "").strip()
    if not link:
        return False
    host = urlparse(link).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in _TELEGRAM_HOSTS


def _extract_telegram_message_ref(link: str) -> str:
    path = [part for part in urlparse(link).path.split("/") if part]
    if not path:
        return ""
    tail = path[-1]
    return tail if tail.isdigit() else ""


def derive_text_title(text: object, *, max_len: int = 120) -> str:
    raw = str(text or "")
    for line in raw.splitlines():
        cleaned = sanitize_text(line)
        if cleaned:
            return to_card_preview_text(cleaned, max_len=max_len)
    cleaned = sanitize_text(raw)
    if cleaned:
        return to_card_preview_text(cleaned, max_len=max_len)
    return ""


def derive_item_title(item: Mapping[str, Any], *, max_len: int = 120) -> str:
    """Осмысленный заголовок для показа в UI/публикации."""
    evidence = linked_article_context(item)
    if evidence and evidence.get("title"):
        return to_card_preview_text(evidence["title"], max_len=max_len)
    title = _normalize_candidate_title(item.get("title"))
    if title and not is_telegram_item(item):
        return to_card_preview_text(title, max_len=max_len) or title

    text_title = derive_text_title(
        item.get("content") or item.get("transcript"), max_len=max_len
    )
    if text_title:
        return text_title

    title = _normalize_candidate_title(item.get("title"))

    source = sanitize_text(item.get("source"))
    if is_telegram_item(item):
        link = str(item.get("link") or item.get("source_url") or "").strip()
        ref = _extract_telegram_message_ref(link)
        if source and ref:
            return f"Пост из {source} #{ref}"
        if source:
            return f"Пост из {source}"
        if ref:
            return f"Пост Telegram #{ref}"
        return "Пост Telegram"

    if title:
        return to_card_preview_text(title, max_len=max_len) or title
    if source:
        return f"Материал из {source}"
    return "Без заголовка"


def missing_text_context_summary(item: Mapping[str, Any]) -> str:
    """Безопасный summary, когда в базе нет текста поста."""
    source = derive_item_title(item, max_len=120)
    text = (
        "В записи нет текстового контента в базе. "
        "Откройте источник и примите решение вручную."
    )
    if source:
        text = f"{text} ({source})"
    return to_card_preview_text(text, max_len=260)


def is_title_only_summary_fallback(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
) -> bool:
    """Определить саммари, построенное без текста по заголовку или одной ссылке."""
    nlp = nlp or {}
    extra = nlp.get("extra")
    evidence = linked_article_context(item)
    if requires_linked_article_context(item) and not evidence:
        return True
    if isinstance(extra, Mapping) and extra.get("source_input_sha256"):
        if extra["source_input_sha256"] != source_input_hash(item):
            return True
    if evidence and (
        not isinstance(extra, Mapping)
        or extra.get("source_text_sha256") != evidence["text_sha256"]
    ):
        return True
    if get_item_text_context(item):
        return False
    if isinstance(extra, Mapping) and extra.get("owner_rewritten") is True:
        return False
    if isinstance(extra, Mapping) and extra.get("source_context_missing") is True:
        return True
    raw_content = sanitize_text(item.get("content"))
    if raw_content and _URL_RE.search(raw_content) and nlp.get("summary"):
        return True
    if str(nlp.get("merged_text") or "").strip():
        return False
    if not isinstance(extra, Mapping):
        return False
    title = sanitize_text(item.get("title"))
    if not title:
        return False
    sanitized_text = sanitize_text(extra.get("sanitized_text"))
    return bool(sanitized_text and sanitized_text == title)


__all__ = [
    "derive_item_title",
    "derive_text_title",
    "get_item_text_context",
    "is_telegram_item",
    "is_title_only_summary_fallback",
    "missing_text_context_summary",
]
