"""Парсинг ссылок t.me для hydrate медиа перед upload на сайт."""

from services.telegram_media_hydrate import _parse_telegram_post


def test_parse_telegram_post_from_link():
    entity, msg = _parse_telegram_post({"link": "https://t.me/wakedivision/519"})
    assert entity == "wakedivision"
    assert msg == 519


def test_parse_telegram_post_from_source_item_id():
    entity, msg = _parse_telegram_post(
        {"source_url": "https://t.me/wakedivision", "source_item_id": "42"}
    )
    assert entity == "wakedivision"
    assert msg == 42


def test_parse_telegram_private_channel():
    entity, msg = _parse_telegram_post({"link": "https://t.me/c/1234567890/77"})
    assert entity == -1001234567890
    assert msg == 77
