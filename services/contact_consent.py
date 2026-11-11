"""Contact / audience consent policy (Content Engine Stage 7).

Контакты — audience intelligence, не инструмент спама.
Агрессивная автоматическая рассылка запрещена.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True, frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str


def evaluate_outreach(
    contact: Mapping[str, Any],
    *,
    has_explicit_consent: bool = False,
    channel: str = "telegram",
) -> ConsentDecision:
    """Запрет auto-outreach без явного consent; всегда запрет mass blast."""
    if channel.lower() in {"blast", "mass", "spam"}:
        return ConsentDecision(False, "mass_outreach_forbidden")
    if not has_explicit_consent:
        return ConsentDecision(False, "consent_required")
    value = str(contact.get("value") or "").strip()
    if not value:
        return ConsentDecision(False, "empty_contact")
    source = str(contact.get("source") or "").strip()
    if not source:
        return ConsentDecision(False, "source_required")
    return ConsentDecision(True, "ok_manual_or_consented")


def contact_intelligence_record(contact: Mapping[str, Any]) -> dict[str, Any]:
    """Нормализованная карточка audience intelligence (без PII в логах вызывающей стороны)."""
    return {
        "contact_id": str(contact.get("contact_id") or ""),
        "type": str(contact.get("type") or ""),
        "source": str(contact.get("source") or ""),
        "interest": str(contact.get("interest") or ""),
        "region": str(contact.get("region") or ""),
        "consent": bool(contact.get("consent")),
        "interaction_history": list(contact.get("interaction_history") or []),
    }


__all__ = [
    "ConsentDecision",
    "contact_intelligence_record",
    "evaluate_outreach",
]
