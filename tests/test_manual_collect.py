from datetime import datetime

from services.manual_collect import (
    ManualSource,
    _convert_raw_entry,
    _take_latest_items,
    parse_period_argument,
)


def test_parse_period_argument():
    p = parse_period_argument("1d")
    assert isinstance(p, datetime)
    p2 = parse_period_argument("3h")
    assert isinstance(p2, datetime)


def test_take_latest_items_keeps_two_freshest():
    items = [
        {"title": "old", "date": "2026-04-15T10:00:00+00:00"},
        {"title": "mid", "date": "2026-04-16T10:00:00+00:00"},
        {"title": "new", "date": "2026-04-17T10:00:00+00:00"},
    ]

    limited = _take_latest_items(items, 2)

    assert [item["title"] for item in limited] == ["mid", "new"]


def test_convert_raw_entry_uses_html_and_media_json_fallbacks():
    source = ManualSource(
        type="website",
        url="https://example.com/blog",
        name="Example",
    )
    entry = {
        "source_type": "website",
        "source_name": "Example",
        "source_url": "https://example.com/blog/post-1",
        "raw_title": "",
        "raw_content": "",
        "raw_html": """
            <article>
                <h1>Wake Event 2026</h1>
                <p>Main update from the article body.</p>
                <img src="/media/cover.jpg" />
            </article>
        """,
        "media_json": '[{"type":"image","url":"https://cdn.example.com/hero.png"}]',
        "created_at": "2026-04-18T10:00:00+00:00",
    }

    converted = _convert_raw_entry(entry, source)

    assert converted is not None
    assert converted["title"] == "Wake Event 2026"
    assert "Main update from the article body." in converted["content"]
    assert converted["images"] == (
        "https://cdn.example.com/hero.png\nhttps://example.com/media/cover.jpg"
    )


def test_convert_raw_entry_skips_empty_telegram_item():
    source = ManualSource(
        type="telegram",
        url="https://t.me/s/wakeflot?after=571",
        name="Wakeflot",
    )
    entry = {
        "source_type": "telegram",
        "source_name": "Wakeflot",
        "source_url": "https://t.me/s/wakeflot?after=571",
        "raw_title": "",
        "raw_content": "",
        "raw_html": "",
        "raw_media": "",
        "media_json": "",
        "created_at": "2026-04-24T10:00:00+00:00",
    }

    assert _convert_raw_entry(entry, source) is None
