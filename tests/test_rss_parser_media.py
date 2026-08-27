from collectors.rss_parser import parse_rss
import collectors.rss_parser as rss_parser_module
from storage.sources import NewsSource


def test_parse_rss_extracts_media_from_html(monkeypatch):
    source = NewsSource("rss", "https://example.com/feed", True, None, "Example RSS")

    entry = {
        "id": "entry-1",
        "title": "Wake Finals",
        "link": "https://example.com/posts/wake-finals",
        "content": [
            {
                "value": """
                    <p>Competition recap</p>
                    <img src="/images/finals.jpg" />
                """,
            }
        ],
    }

    class Feed:
        entries = [entry]

    monkeypatch.setattr(
        rss_parser_module,
        "parse_feed_from_url",
        lambda url, source_label=None: (Feed(), "ok"),
    )

    results = parse_rss(source, [])

    assert len(results) == 1
    assert "https://example.com/images/finals.jpg" in results[0]["raw_media"]
