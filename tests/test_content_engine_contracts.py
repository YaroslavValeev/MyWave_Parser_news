"""Stage 4/3/7 contract helpers."""
from __future__ import annotations

from services.contact_consent import evaluate_outreach
from services.editorial_layers import extract_editorial_layers, publication_allowed
from services.knowledge_contract import build_knowledge_candidate, export_allowed
from services.semantic_dedup import event_fingerprint, maybe_attach_event_id, semantic_dedup_enabled
from services.channel_adapters import list_mvp_adapters


def test_editorial_layers_and_gate():
    layers = extract_editorial_layers(
        {"content": "Факт из источника", "comment": ""},
        {"summary": "Кратко", "author_notes": "Мнение владельца"},
    )
    ok, reason = publication_allowed(layers)
    assert ok and reason == "ok"
    bad, why = publication_allowed({**layers, "owner_commentary": ""})
    assert not bad and why == "owner_commentary_required"


def test_semantic_dedup_off_by_default(monkeypatch):
    monkeypatch.delenv("SEMANTIC_DEDUP_ENABLED", raising=False)
    assert semantic_dedup_enabled() is False
    assert maybe_attach_event_id({"title": "A"}, {"summary": "B"}) is None
    assert len(event_fingerprint("Title One", "Summary")) == 32


def test_consent_blocks_spam():
    d = evaluate_outreach(
        {"value": "@user", "source": "https://t.me/x"},
        has_explicit_consent=False,
    )
    assert d.allowed is False
    blast = evaluate_outreach(
        {"value": "@user", "source": "https://t.me/x"},
        has_explicit_consent=True,
        channel="blast",
    )
    assert blast.allowed is False


def test_knowledge_not_auto_export():
    c = build_knowledge_candidate({"id": 1, "title": "T", "link": "https://x"})
    ok, reason = export_allowed(c)
    assert not ok and reason == "not_approved_for_kb"


def test_mvp_channel_adapters():
    names = {a.name for a in list_mvp_adapters()}
    assert names == {"telegram", "blog"}
