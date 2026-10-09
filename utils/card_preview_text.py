"""
Plain text и публичный body для карточек витрины / анонсов в raw_feed.

Публичные поля не должны содержать сырой Markdown/AI-служебные маркеры.
final_posts/content_md/text также проходят мягкую нормализацию: это контент для сайта,
а не черновик AI.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping

_WS = re.compile(r"\s+")
# [label](url) или ![alt](url)
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# http(s) URL (жадность умеренная — до пробела/скобки/кавычки)
_URL_RE = re.compile(r"https?://[^\s\]\)>'\"]+", re.IGNORECASE)
_PROMPT_ROLE_RE = re.compile(r"^\s*(system|assistant|user|developer|prompt)\s*:\s*", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_RE = re.compile(r"```+")
_ARTIFACT_CHECKS = (
    ("markdown_bold", re.compile(r"\*\*")),
    ("markdown_underscore", re.compile(r"__")),
    ("markdown_heading", re.compile(r"(^|\n)\s*#{1,6}\s+")),
    ("ai_role", re.compile(r"(^|\n)\s*(system|assistant|user|developer|prompt)\s*:", re.IGNORECASE)),
    ("code_fence", re.compile(r"```+")),
    ("model_token", re.compile(r"<\|[^|]+?\|>")),
    ("repeated_markup", re.compile(r"([*_#])\1{2,}")),
)


def _strip_emphasis(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"__([^_]+)__", r"\1", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
        s = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", s)
    return s


def _strip_inline_code(s: str) -> str:
    return re.sub(r"`+([^`]+)`+", r"\1", s)


def _strip_headers(s: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)


def _strip_prompt_role_lines(s: str) -> str:
    lines = []
    for line in s.splitlines():
        if _PROMPT_ROLE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _strip_markdown_blocks(s: str) -> str:
    s = _HTML_COMMENT_RE.sub("", s)
    s = _FENCE_RE.sub("", s)
    s = re.sub(r"<\|[^|]+?\|>", "", s)
    return s


def strip_markdown_to_plain(text: str) -> str:
    """Убрать типичный Markdown и разметку ссылок; вернуть одну строку с пробелами."""
    if not text:
        return ""
    s = html.unescape(str(text).strip())
    if not s:
        return ""
    s = _strip_markdown_blocks(s)
    s = _strip_prompt_role_lines(s)
    s = _MD_LINK.sub(r"\1", s)
    s = _strip_inline_code(s)
    s = _strip_emphasis(s)
    s = _strip_headers(s)
    s = s.replace("•", "·").replace("▪", " ")
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = s.replace("*", "")
    return s


def normalize_publication_text(text: str | None, *, preserve_paragraphs: bool = True) -> str:
    """
    Очистить публичный body/title от Markdown/AI-артефактов без агрессивного рерайта.

    В отличие от карточки, сохраняет абзацы, если ``preserve_paragraphs=True``.
    """
    if not text:
        return ""
    s = html.unescape(str(text).strip())
    if not s:
        return ""
    s = _strip_markdown_blocks(s)
    s = _strip_prompt_role_lines(s)
    s = _MD_LINK.sub(r"\1", s)
    s = _strip_inline_code(s)
    s = _strip_emphasis(s)
    s = _strip_headers(s)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"(?<!\w)_{2,}(?!\w)", "", s)
    s = re.sub(r"\*{2,}", "", s)
    if not preserve_paragraphs:
        return collapse_whitespace(s)
    return s.strip()


# Редакционный эталон Blog (ParserNews)
PUBLIC_TITLE_MAX_LEN = 90
PUBLIC_LEAD_MAX_LEN = 200
PUBLIC_BODY_MIN_PARAGRAPHS = 2
PUBLIC_BODY_MAX_PARAGRAPHS = 5

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_VIDEO_HOST_HINT = re.compile(
    r"(youtube\.com|youtu\.be|rutube\.ru|vimeo\.com|kinescope\.io|vk\.com/video|vk\.ru/video)",
    re.IGNORECASE,
)


def normalize_public_title(text: str | None, *, max_len: int = PUBLIC_TITLE_MAX_LEN) -> str:
    return truncate_for_card(
        normalize_publication_text(text, preserve_paragraphs=False),
        max_len=max_len,
    )


def lead_from_text(text: str | None, *, max_len: int = PUBLIC_LEAD_MAX_LEN) -> str:
    """Lead: 1–2 предложения, plain, без URL."""
    plain = to_card_preview_text(text, max_len=max(max_len * 2, 400))
    if not plain:
        return ""
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(plain) if p.strip()]
    if not parts:
        return truncate_for_card(plain, max_len)
    lead = " ".join(parts[:2])
    return truncate_for_card(lead, max_len)


def count_paragraphs(text: str | None) -> int:
    body = normalize_publication_text(text, preserve_paragraphs=True)
    if not body:
        return 0
    return len([p for p in re.split(r"\n\s*\n", body) if p.strip()])


def strip_embedded_video_urls(text: str | None) -> str:
    """Убрать голые video/provider URL из body — они должны жить в video_url/embed_url."""
    if not text:
        return ""
    lines: list[str] = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if _URL_RE.fullmatch(stripped) and _VIDEO_HOST_HINT.search(stripped):
            continue
        if _path_is_direct_video_url(stripped):
            continue
        cleaned = _URL_RE.sub(
            lambda m: "" if _VIDEO_HOST_HINT.search(m.group(0)) or _path_is_direct_video_url(m.group(0)) else m.group(0),
            line,
        )
        lines.append(cleaned.rstrip())
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _path_is_direct_video_url(value: str) -> bool:
    text = (value or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    path = text.split("?", 1)[0].lower()
    return path.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"))


def editorial_structure_issues(
    *,
    title: str = "",
    lead: str = "",
    body: str = "",
    source_name: str = "",
    source_url: str = "",
) -> list[str]:
    """Редакционные стоп-критерии до READY_TO_PUBLISH."""
    issues: list[str] = []
    clean_title = collapse_whitespace(title)
    if not clean_title:
        issues.append("title_empty")
    elif len(clean_title) > PUBLIC_TITLE_MAX_LEN:
        issues.append("title_too_long")

    clean_lead = collapse_whitespace(lead)
    if not clean_lead:
        issues.append("lead_empty")
    else:
        sentences = [p for p in _SENTENCE_SPLIT.split(clean_lead) if p.strip()]
        if len(sentences) > 2:
            issues.append("lead_too_many_sentences")

    paragraphs = count_paragraphs(body)
    if paragraphs and paragraphs < PUBLIC_BODY_MIN_PARAGRAPHS:
        issues.append("body_too_few_paragraphs")
    elif paragraphs > PUBLIC_BODY_MAX_PARAGRAPHS:
        issues.append("body_too_many_paragraphs")

    if body and any(
        _VIDEO_HOST_HINT.search(m.group(0)) or _path_is_direct_video_url(m.group(0))
        for m in _URL_RE.finditer(body)
    ):
        issues.append("video_url_in_body")

    if not str(source_name or "").strip():
        issues.append("source_name_missing")
    if not str(source_url or "").strip():
        issues.append("source_url_missing")
    return issues


def remove_bare_urls(text: str) -> str:
    """Удалить «голые» URL из текста анонса (остаётся обычный текст)."""
    if not text:
        return ""
    return _URL_RE.sub("", text).strip()


def collapse_whitespace(text: str) -> str:
    return _WS.sub(" ", (text or "").strip()).strip()


def truncate_for_card(text: str, max_len: int) -> str:
    """Обрезка по границе предложения или слова + многоточие."""
    if max_len <= 0:
        return ""
    s = text or ""
    if len(s) <= max_len:
        return s
    cut = s[: max_len]
    for sep in (". ", "! ", "? ", "… ", ": "):
        idx = cut.rfind(sep)
        if idx >= max(20, max_len // 3):
            return cut[: idx + len(sep.rstrip())].rstrip() + "…"
    sp = cut.rfind(" ", 10, max_len)
    if sp > 0:
        return cut[:sp].rstrip() + "…"
    return cut[: max_len - 1].rstrip() + "…"


def to_card_preview_text(text: str | None, *, max_len: int = 260) -> str:
    """
    Готовый plain text для карточки блога: без MD, без длинных URL, с разумной длиной.
    """
    s = strip_markdown_to_plain(text or "")
    s = remove_bare_urls(s)
    s = collapse_whitespace(s)
    return truncate_for_card(s, max_len)


def public_text_quality_issues(
    *,
    title: str = "",
    excerpt: str = "",
    body: str = "",
    lead: str = "",
    source_name: str = "",
    source_url: str = "",
    enforce_editorial: bool = False,
) -> list[str]:
    """
    Проверить публичные поля перед публикацией.

    Возвращает короткие коды проблем; пустой список означает, что явных
    Markdown/AI-артефактов не найдено.
    """
    issues: list[str] = []
    joined = "\n".join(part for part in (title, excerpt, lead, body) if part)
    for code, pattern in _ARTIFACT_CHECKS:
        if pattern.search(joined):
            issues.append(code)
    clean_excerpt = collapse_whitespace(excerpt)
    clean_body = collapse_whitespace(body)
    if not clean_excerpt:
        issues.append("excerpt_empty")
    elif clean_body and len(clean_excerpt) < 24 and len(clean_body) >= 80:
        issues.append("excerpt_too_short")
    if enforce_editorial:
        issues.extend(
            editorial_structure_issues(
                title=title,
                lead=lead or excerpt,
                body=body,
                source_name=source_name,
                source_url=source_url,
            )
        )
    # de-dupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for code in issues:
        if code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


# Поля raw_feed, которые ведут на витрину как строка без MD
_CARD_PLAIN_FIELDS = ("summary", "excerpt", "lead", "meta_description", "og_description")
_FINAL_KEY = "final_posts"


def normalize_raw_feed_card_fields(item: Mapping[str, Any] | dict[str, Any]) -> None:
    """
    Мутирует dict строки raw_feed перед записью в Sheets:

    - Нормализует непустые summary/excerpt/lead/meta_description/og_description.
    - Если excerpt пуст, а final_posts заполнен — excerpt = превью из final_posts (plain).
    - Если summary или lead пусты при непустом final_posts — заполняются тем же превью (как на сайте).
    - Пустой final_posts не компенсируется длинным текстом из других полей (сырой контент сюда не подмешиваем).

    meta_description / og_description при пустом значении заполняются коротким превью из final_posts
    (разные лимиты для SEO/OG).
    """
    if not isinstance(item, dict):
        return

    for key in ("text", "final_version", "content_md", _FINAL_KEY):
        raw = str(item.get(key) or "").strip()
        if raw:
            item[key] = normalize_publication_text(
                strip_embedded_video_urls(raw),
                preserve_paragraphs=True,
            )

    fp_source = str(item.get(_FINAL_KEY) or "").strip()

    for key in ("raw_title", "title", "seo_title", "og_title"):
        if str(item.get(key) or "").strip():
            item[key] = normalize_public_title(str(item.get(key) or ""), max_len=PUBLIC_TITLE_MAX_LEN)

    for key in _CARD_PLAIN_FIELDS:
        raw = item.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s:
            if key == "lead":
                item[key] = lead_from_text(s, max_len=PUBLIC_LEAD_MAX_LEN)
            else:
                max_len = 160 if key == "meta_description" else (200 if key == "og_description" else 260)
                item[key] = to_card_preview_text(s, max_len=max_len)
        else:
            item[key] = ""

    preview_from_final = to_card_preview_text(fp_source, max_len=260) if fp_source else ""
    lead_from_final = lead_from_text(fp_source, max_len=PUBLIC_LEAD_MAX_LEN) if fp_source else ""

    if not str(item.get("excerpt") or "").strip() and preview_from_final:
        item["excerpt"] = preview_from_final
    if not str(item.get("summary") or "").strip() and preview_from_final:
        item["summary"] = preview_from_final
    if not str(item.get("lead") or "").strip() and lead_from_final:
        item["lead"] = lead_from_final

    if not str(item.get("meta_description") or "").strip() and preview_from_final:
        item["meta_description"] = to_card_preview_text(fp_source, max_len=160)
    if not str(item.get("og_description") or "").strip() and preview_from_final:
        item["og_description"] = to_card_preview_text(fp_source, max_len=200)


__all__ = [
    "PUBLIC_BODY_MAX_PARAGRAPHS",
    "PUBLIC_BODY_MIN_PARAGRAPHS",
    "PUBLIC_LEAD_MAX_LEN",
    "PUBLIC_TITLE_MAX_LEN",
    "collapse_whitespace",
    "count_paragraphs",
    "editorial_structure_issues",
    "lead_from_text",
    "normalize_raw_feed_card_fields",
    "normalize_public_title",
    "normalize_publication_text",
    "public_text_quality_issues",
    "remove_bare_urls",
    "strip_embedded_video_urls",
    "strip_markdown_to_plain",
    "to_card_preview_text",
    "truncate_for_card",
]
