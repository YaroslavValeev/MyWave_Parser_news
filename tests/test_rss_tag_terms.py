"""Теги RSS: FeedParserDict, не str."""

from collectors.rss_parser import _rss_tag_terms


def test_rss_tag_terms_from_feedparser_dicts():
    entry = {
        "tags": [
            {"term": "wake", "scheme": None, "label": None},
            {"term": "sports", "scheme": None, "label": None},
        ]
    }
    assert _rss_tag_terms(entry) == "wake,sports"


def test_rss_tag_terms_empty():
    assert _rss_tag_terms({}) == ""
    assert _rss_tag_terms({"tags": []}) == ""
