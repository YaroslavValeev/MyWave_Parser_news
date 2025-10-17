"""Правила маршрутизации результатов NLP."""
from __future__ import annotations

from typing import Mapping, Sequence

PUBLISH = "publish"
REVIEW = "review"
DISCARD = "discard"


def decide_route(
    summary: str | None,
    questions: Sequence[str] | None,
    moderation: Mapping[str, object] | None,
) -> str:
    """Выбрать маршрут обработки элемента.

    Стратегия основана на консервативных эвристиках: при наличии флага модерации
    материал отклоняется, короткие или пустые саммари отправляются на ручную
    проверку, иначе материал готов к публикации.
    """

    if moderation:
        flagged = moderation.get("flagged")
        if isinstance(flagged, bool) and flagged:
            return DISCARD
        categories = moderation.get("categories")
        if isinstance(categories, Mapping):
            # если обнаружена токсичная категория с высоким значением
            for score in categories.values():
                if isinstance(score, (int, float)) and score >= 0.7:
                    return DISCARD

    if not summary or len(summary.split()) < 5:
        return REVIEW

    if questions:
        unanswered = []
        for question in questions:
            tokens = [token for token in question.replace("?", " ").split() if token.strip()]
            if len(tokens) <= 1:
                unanswered.append(question)
        if unanswered:
            return REVIEW

    return PUBLISH


__all__ = ["decide_route", "PUBLISH", "REVIEW", "DISCARD"]
