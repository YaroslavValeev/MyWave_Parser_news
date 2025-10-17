from nlp.routing import decide_route, PUBLISH, REVIEW, DISCARD


def test_decide_route_publish():
    assert decide_route("This is a valid summary with many words", [], None) == PUBLISH


def test_decide_route_review_short_summary():
    assert decide_route("Hi", [], None) == REVIEW


def test_decide_route_discard_moderation():
    mod = {"flagged": True}
    assert decide_route(None, None, mod) == DISCARD
