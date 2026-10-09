import json

from collectors.telegram_parser import (
    _finalize_entries,
    _has_review_payload,
    _merge_grouped_entries,
)


def test_merge_grouped_entries_uses_album_caption_and_all_media():
    entries = [
        {
            "id": "1619",
            "source_item_id": "1619",
            "link": "https://t.me/wakestyleclub/1619",
            "raw_title": "Пост из Вейкстайл Клуб #1619",
            "raw_content": "",
            "raw_media": '["/static/downloads/photo.jpg"]',
            "media_json": json.dumps(
                {
                    "type": "image",
                    "post_url": "https://t.me/wakestyleclub/1619",
                    "url": "/static/downloads/photo.jpg",
                },
                ensure_ascii=False,
            ),
            "cover_image_url": "/static/downloads/photo.jpg",
            "debug_info": "msg_id=1619",
            "_telegram_grouped_id": "777",
        },
        {
            "id": "1618",
            "source_item_id": "1618",
            "link": "https://t.me/wakestyleclub/1618",
            "raw_title": "Готовимся проверяем малышек после зимы",
            "raw_content": "Готовимся проверяем малышек после зимы",
            "raw_media": '["/static/downloads/video.mov"]',
            "media_json": json.dumps(
                {
                    "type": "video",
                    "post_url": "https://t.me/wakestyleclub/1618",
                    "url": "/static/downloads/video.mov",
                },
                ensure_ascii=False,
            ),
            "cover_image_url": "",
            "debug_info": "msg_id=1618",
            "_telegram_grouped_id": "777",
        },
    ]

    merged = _merge_grouped_entries(entries)

    assert len(merged) == 1
    item = merged[0]
    assert item["id"] == "1618"
    assert item["source_item_id"] == "album:777"
    assert item["raw_content"] == "Готовимся проверяем малышек после зимы"
    assert item["link"] == "https://t.me/wakestyleclub/1618"
    assert item["cover_image_url"] == "/static/downloads/photo.jpg"
    assert "/static/downloads/photo.jpg" in item["raw_media"]
    assert "/static/downloads/video.mov" in item["raw_media"]
    assert "merged_msg_ids=1619,1618" in item["debug_info"]


def test_empty_telegram_entry_without_text_or_media_is_not_review_payload():
    assert not _has_review_payload(
        {
            "id": "1875",
            "raw_title": "Пост из Wakediary #1875",
            "raw_content": "",
            "raw_media": "",
            "media_json": json.dumps(
                {"type": "telegram_post", "post_url": "https://t.me/wakediary/1875"},
                ensure_ascii=False,
            ),
            "cover_image_url": "",
            "parse_error": "",
        }
    )


def test_album_without_downloads_keeps_caption_and_media_flag():
    """Прод: TELEGRAM_SKIP_MEDIA_FULL_COLLECT=true — файлов нет, но альбом должен стать одной новостью."""
    entries = [
        {"id": "21346", "raw_content": "", "raw_media": "[]", "_telegram_grouped_id": "9", "_telegram_has_media": True},
        {"id": "21345", "raw_content": "", "raw_media": "[]", "_telegram_grouped_id": "9", "_telegram_has_media": True},
        {"id": "21344", "raw_content": "Улетаю на чемпионат мира", "raw_media": "[]",
         "_telegram_grouped_id": "9", "_telegram_has_media": True},
        {"id": "21343", "raw_content": "Обычный пост", "raw_media": "[]", "_telegram_grouped_id": ""},
    ]

    merged = _merge_grouped_entries(entries)

    assert [e["id"] for e in merged] == ["21344", "21343"]
    assert merged[0]["raw_content"] == "Улетаю на чемпионат мира"
    assert _has_review_payload(merged[0])


def test_finalize_drops_empty_and_private_keys():
    entries = [
        {"id": "1", "raw_content": "", "raw_media": "[]", "_telegram_grouped_id": "", "_telegram_has_media": False},
        {"id": "2", "raw_content": "", "raw_media": "[]", "_telegram_grouped_id": "", "_telegram_has_media": True},
    ]

    result = _finalize_entries(entries, "https://t.me/x")

    assert [e["id"] for e in result] == ["2"]
    assert "_telegram_has_media" not in result[0]
    assert "_telegram_grouped_id" not in result[0]
    assert entries == []


def test_text_only_telegram_entry_is_review_payload():
    assert _has_review_payload(
        {
            "id": "1876",
            "raw_content": "Федерация водных лыж опубликовала расписание.",
            "raw_media": "",
            "media_json": "",
            "cover_image_url": "",
            "parse_error": "",
        }
    )
