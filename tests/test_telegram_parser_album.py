import json

from collectors.telegram_parser import _has_review_payload, _merge_grouped_entries


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
