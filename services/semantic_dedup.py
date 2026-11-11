"""Semantic / event-level dedup (Content Engine Stage 3) — scaffold.

По умолчанию выключен. Technical dedup (checksum / source_item_id) остаётся каноном.
Включение: SEMANTIC_DEDUP_ENABLED=true после доказанного E2E (Stage 2).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Mapping


def semantic_dedup_enabled() -> bool:
    return os.getenv("SEMANTIC_DEDUP_ENABLED", "false").lower() == "true"


def _normalize_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\sа-яё]", "", t, flags=re.IGNORECASE)
    return t.strip()[:500]


def event_fingerprint(title: str, summary: str = "") -> str:
    """Детерминированный отпечаток события (не embedding). Для будущего cluster id."""
    base = _normalize_text(f"{title} {summary}")
    if not base:
        return ""
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def maybe_attach_event_id(item: Mapping[str, Any], nlp: Mapping[str, Any] | None = None) -> str | None:
    """Вернуть event_id только если feature flag включён; иначе None (no-op)."""
    if not semantic_dedup_enabled():
        return None
    nlp = nlp or {}
    title = str(item.get("title") or item.get("raw_title") or "")
    summary = str(nlp.get("summary") or item.get("content") or "")[:400]
    fp = event_fingerprint(title, summary)
    return fp or None


__all__ = [
    "event_fingerprint",
    "maybe_attach_event_id",
    "semantic_dedup_enabled",
]
