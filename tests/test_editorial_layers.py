from services.editorial_layers import (
    LAYER_OWNER_COMMENTARY,
    extract_editorial_layers,
    publication_allowed,
)


def test_publication_blocked_without_owner_commentary():
    layers = extract_editorial_layers(
        {"content": "Факт из источника"},
        {"summary": "Саммари"},
    )
    allowed, reason = publication_allowed(layers)
    assert allowed is False
    assert reason == "owner_commentary_required"
    assert layers[LAYER_OWNER_COMMENTARY] == ""


def test_publication_allowed_with_owner_notes():
    layers = extract_editorial_layers(
        {"content": "Факт"},
        {"summary": "Саммари", "author_notes": "Комментарий owner"},
    )
    allowed, reason = publication_allowed(layers)
    assert allowed is True
    assert reason == "ok"
