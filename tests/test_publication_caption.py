"""NLP не публикует сам; caption = саммари + комментарий + футер."""
from __future__ import annotations

from nlp.routing import DISCARD, PUBLISH, REVIEW
from services.nlp_pipeline import STATUS_MAP


def test_nlp_publish_decision_goes_to_review_not_approved():
    assert STATUS_MAP[PUBLISH] == "review"
    assert STATUS_MAP[REVIEW] == "review"
    assert STATUS_MAP[DISCARD] == "discarded"
