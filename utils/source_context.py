"""Source-bound evidence for linked articles; independent of derived NLP output."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from nlp.sanitize import sanitize_text

MAX_ARTICLE_TEXT = 30000
_URLS = re.compile(r'https?://[^\s<>"\'\u200b]+', re.I)


def source_input_hash(item: Mapping[str, Any]) -> str:
    fields = {key: item.get(key) for key in ("id", "content", "transcript", "link")}
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def normalized_article_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsupported_article_url")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("unsupported_article_url")
    return urlunsplit(
        (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )


def article_urls(item: Mapping[str, Any]) -> list[str]:
    """Prefer links explicitly in the post; use its own link only when empty."""
    raw = sanitize_text(item.get("content")) or sanitize_text(item.get("transcript"))
    urls = _URLS.findall(raw) if raw else [str(item.get("link") or "")]
    found = []
    for value in urls:
        try:
            url = normalized_article_url(value.rstrip(").,;]"))
        except ValueError:
            continue
        if url not in found:
            found.append(url)
    return found


def linked_article_context(item: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = item.get("source_context")
    try:
        evidence = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(evidence, Mapping) or evidence.get("version") != 1:
        return None
    if evidence.get("input_sha256") != source_input_hash(item):
        return None
    text = evidence.get("text")
    if not isinstance(text, str) or not 200 <= len(text) <= MAX_ARTICLE_TEXT:
        return None
    if hashlib.sha256(text.encode()).hexdigest() != evidence.get("text_sha256"):
        return None
    urls = article_urls(item)
    if len(urls) != 1 or evidence.get("requested_url") != urls[0]:
        return None
    try:
        requested = urlsplit(urls[0])
        final = urlsplit(normalized_article_url(str(evidence.get("final_url") or "")))
    except ValueError:
        return None
    if (
        requested.hostname != final.hostname
        or requested.path.rstrip("/") != final.path.rstrip("/")
        or requested.query != final.query
    ):
        return None
    if requested.scheme == "https" and final.scheme != "https":
        return None
    return dict(evidence)
