"""Editorial integrity layers (Content Engine Stage 4 contract helpers).

Разделение слоёв контента. Публикация без Owner commentary запрещена policy.
"""
from __future__ import annotations

from typing import Any, Mapping


LAYER_SOURCE_FACT = "source_fact"
LAYER_AUTO_SUMMARY = "auto_summary"
LAYER_OWNER_COMMENTARY = "owner_commentary"
LAYER_INTERPRETATION = "interpretation"


def extract_editorial_layers(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Вытащить слои без смешивания в один текст."""
    nlp = nlp or {}
    return {
        LAYER_SOURCE_FACT: str(
            item.get("content") or item.get("raw_content") or item.get("title") or ""
        ).strip(),
        LAYER_AUTO_SUMMARY: str(nlp.get("summary") or "").strip(),
        LAYER_OWNER_COMMENTARY: str(
            nlp.get("author_notes") or item.get("comment") or item.get("expert_opinion") or ""
        ).strip(),
        LAYER_INTERPRETATION: str(
            (nlp.get("extra") or {}).get("interpretation")
            if isinstance(nlp.get("extra"), Mapping)
            else nlp.get("interpretation") or ""
        ).strip(),
    }


def publication_allowed(layers: Mapping[str, str]) -> tuple[bool, str]:
    """Editorial policy: без Owner commentary публикация запрещена."""
    if not (layers.get(LAYER_OWNER_COMMENTARY) or "").strip():
        return False, "owner_commentary_required"
    if not (layers.get(LAYER_SOURCE_FACT) or layers.get(LAYER_AUTO_SUMMARY) or "").strip():
        return False, "no_source_or_summary"
    return True, "ok"


__all__ = [
    "LAYER_AUTO_SUMMARY",
    "LAYER_INTERPRETATION",
    "LAYER_OWNER_COMMENTARY",
    "LAYER_SOURCE_FACT",
    "extract_editorial_layers",
    "publication_allowed",
]
