"""Перевод и сборка owner-facing текста для ревью и публикации."""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from config.settings import config
from utils.card_preview_text import normalize_public_title, normalize_publication_text, to_card_preview_text
from utils.item_context import derive_item_title, get_item_text_context
from utils.russian_summary import is_probably_non_russian, wants_russian

LOGGER = logging.getLogger(__name__)

_AUTHOR_LABEL_RE = re.compile(
    r"(?im)^\s*(Личная заметка|Мнение автора|Комментарий автора|Авторское мнение|"
    r"Ваш комментарий|Саммари)\s*:?\s*"
)


def strip_author_meta_labels(text: str | None) -> str:
    cleaned = _AUTHOR_LABEL_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_fallback_merged_text(
    *,
    source_text: str,
    author_notes: str,
    title: str | None = None,
    max_len: int = 3500,
) -> str:
    """Собрать пост: саммари/краткий текст + комментарий Owner почти без обработки."""
    body = normalize_publication_text(source_text, preserve_paragraphs=True)
    # Комментарий Owner — минимальная нормализация (без LLM и без служебных ярлыков).
    notes = strip_author_meta_labels(author_notes)
    notes = re.sub(r"\n{3,}", "\n\n", notes).strip()
    heading = normalize_public_title(title or "", max_len=160)
    parts: list[str] = []
    if heading:
        parts.append(heading)
    if body:
        parts.append(body)
    if notes:
        parts.append(notes)
    merged = "\n\n".join(parts).strip()
    if not merged:
        return notes or body
    return to_card_preview_text(merged, max_len=max_len) if max_len else merged


async def translate_text_for_owner(
    text: str,
    *,
    lang: str | None = None,
    max_len: int = 4000,
) -> str:
    """Перевести иностранный текст на язык редактора (по умолчанию RU)."""
    source = normalize_publication_text(text, preserve_paragraphs=True)
    if not source:
        return ""
    target_lang = lang or config.NL_LANG
    if not wants_russian(target_lang) or not is_probably_non_russian(source):
        return source
    if not config.OPENAI_API_KEY:
        return source

    from nlp.openai_client import get_openai_client

    client = await get_openai_client()
    try:
        translated = await client.translate_text(source, lang=target_lang, max_len=max_len)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("owner translation failed: %s", exc)
        return source
    translated = normalize_publication_text(translated, preserve_paragraphs=True)
    return translated or source


def owner_editing_text(item: Mapping[str, Any], nlp: Mapping[str, Any] | None = None) -> str:
    """Текст, который owner видит для редактирования (предпочтительно перевод)."""
    nlp = nlp or {}
    extra = nlp.get("extra")
    if isinstance(extra, Mapping):
        cached = str(extra.get("owner_editing_text") or extra.get("translated_text") or "").strip()
        if cached:
            return cached
    return get_item_text_context(item)


async def ensure_owner_editing_context(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    """Подготовить перевод для карточки ревью; вернуть item/nlp/editing_text."""
    nlp = dict(nlp or {})
    extra = dict(nlp.get("extra") or {}) if isinstance(nlp.get("extra"), Mapping) else {}
    source_text = get_item_text_context(item)
    editing_text = owner_editing_text(item, nlp)
    cached_editing = str(extra.get("owner_editing_text") or "").strip()

    should_translate_body = (
        source_text
        and wants_russian(config.NL_LANG)
        and is_probably_non_russian(source_text)
        and (not cached_editing or is_probably_non_russian(cached_editing))
    )
    if should_translate_body:
        translated = await translate_text_for_owner(source_text)
        if translated and translated != source_text:
            extra["owner_editing_text"] = translated
            extra["source_lang"] = "foreign"
            editing_text = translated
        elif not config.OPENAI_API_KEY:
            extra["translation_skipped"] = "no_openai_key"

    title_src = derive_item_title(item, max_len=160)
    cached_title = str(extra.get("owner_display_title") or "").strip()
    should_translate_title = (
        title_src
        and wants_russian(config.NL_LANG)
        and is_probably_non_russian(title_src)
        and (not cached_title or is_probably_non_russian(cached_title))
    )
    if should_translate_title:
        translated_title = await translate_text_for_owner(title_src, max_len=220)
        if translated_title and translated_title != title_src:
            extra["owner_display_title"] = translated_title

    if extra:
        nlp["extra"] = extra
    return item, nlp, editing_text or source_text


async def ensure_merged_owner_post(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any],
    *,
    force: bool = False,
) -> str:
    """Собрать merged_text: по умолчанию саммари + почти сырой комментарий Owner."""
    merged = str(nlp.get("merged_text") or "").strip()
    if merged and not force:
        return merged

    notes = str(nlp.get("author_notes") or "").strip()
    if not notes:
        return merged

    summary = str(nlp.get("summary") or "").strip()
    # Канон Owner: из оригинала оставляем саммари, не полный raw_content.
    source_text = summary or owner_editing_text(item, nlp)
    if not source_text:
        return merged

    title = derive_item_title(item, max_len=160)

    use_llm = bool(getattr(config, "OWNER_POST_USE_LLM_REWRITE", False))
    if (
        use_llm
        and config.OPENAI_API_KEY
        and not __import__("os").getenv("PYTEST_CURRENT_TEST")
    ):
        from nlp.openai_client import get_openai_client

        try:
            client = await get_openai_client()
            rewritten = await client.author_rewrite(
                source_text,
                notes,
                lang=config.NL_LANG,
            )
            rewritten = strip_author_meta_labels(rewritten)
            if rewritten:
                return normalize_publication_text(rewritten, preserve_paragraphs=True)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("ensure_merged_owner_post rewrite failed: %s", exc)

    return build_fallback_merged_text(
        source_text=source_text,
        author_notes=notes,
        title=title,
    )


__all__ = [
    "build_fallback_merged_text",
    "ensure_merged_owner_post",
    "ensure_owner_editing_context",
    "owner_editing_text",
    "strip_author_meta_labels",
    "translate_text_for_owner",
]
