"""Knowledge export contract (Content Engine Stage 6) — scaffold only.

Активируется только после доказанного Content E2E.
Публикация ≠ автоматическое знание; это кандидат на extraction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class KnowledgeCandidate:
    """Кандидат в общую KB MyWave (не локальная KB ParserNews)."""

    content_id: str
    source_url: str
    title: str
    entities: dict[str, list[str]] = field(default_factory=dict)
    relations: list[dict[str, str]] = field(default_factory=list)
    approved_for_kb: bool = False


ENTITY_KEYS = (
    "people",
    "athletes",
    "clubs",
    "competitions",
    "disciplines",
    "results",
    "rules",
    "method_insights",
    "dates",
    "places",
    "organizations",
)


def empty_entities() -> dict[str, list[str]]:
    return {k: [] for k in ENTITY_KEYS}


def build_knowledge_candidate(
    item: Mapping[str, Any],
    *,
    entities: Mapping[str, list[str]] | None = None,
    approved_for_kb: bool = False,
) -> KnowledgeCandidate:
    ent = empty_entities()
    if entities:
        for key, values in entities.items():
            if key in ent and isinstance(values, list):
                ent[key] = [str(v) for v in values if str(v).strip()]
    return KnowledgeCandidate(
        content_id=str(item.get("id") or item.get("news_id") or ""),
        source_url=str(item.get("link") or item.get("source_url") or ""),
        title=str(item.get("title") or item.get("raw_title") or ""),
        entities=ent,
        approved_for_kb=approved_for_kb,
    )


def export_allowed(candidate: KnowledgeCandidate) -> tuple[bool, str]:
    if not candidate.approved_for_kb:
        return False, "not_approved_for_kb"
    if not candidate.content_id:
        return False, "missing_content_id"
    return True, "ok"


__all__ = [
    "ENTITY_KEYS",
    "KnowledgeCandidate",
    "build_knowledge_candidate",
    "empty_entities",
    "export_allowed",
]
