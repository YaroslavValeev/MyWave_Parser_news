import json
from importlib import import_module

from services.raw_feed_sync import build_ingest_row
from utils.row_utils import validate_raw_row
from utils.media_utils import (
    build_media_contract_diagnostic,
    extract_cover_image_url,
    extract_raw_feed_cover_image_url,
    extract_source_media_url,
    is_telegram_post_url,
    is_telegram_url,
    media_contract_is_publishable,
    media_path_to_public_url,
    media_path_to_raw_feed_url,
    normalize_media_contract_fields,
    sanitize_media_json_payload,
    sanitize_raw_media_payload,
)


def _set_media_export_config(
    monkeypatch,
    *,
    base_url: str = "",
    allow_static: bool = False,
    site_base_url: str = "",
) -> None:
    settings = import_module("config.settings")
    monkeypatch.setattr(settings.config, "PUBLIC_MEDIA_BASE_URL", base_url)
    monkeypatch.setattr(settings.config, "ALLOW_RELATIVE_STATIC_MEDIA_IN_RAW_FEED", allow_static)
    monkeypatch.setattr(settings.config, "SITE_BASE_URL", site_base_url)


def test_telegram_post_url_is_not_media():
    assert is_telegram_post_url("https://t.me/wakedivision/519") is True
    assert is_telegram_url("https://t.me/wakedivision") is True
    assert sanitize_raw_media_payload(["https://t.me/wakedivision/519"]) == ""
    assert sanitize_raw_media_payload(["https://t.me/s/wakedivision/519"]) == ""
    assert sanitize_raw_media_payload(["https://t.me/wakedivision"]) == ""
    assert extract_cover_image_url({"images": "https://t.me/wakedivision/519"}) == ""


def test_media_json_prefers_thumbnail_over_post_url():
    media_json = {
        "type": "telegram_post",
        "url": "https://t.me/wakedivision/519",
        "thumbnail_url": "https://cdn.example.com/cover.webp",
    }

    sanitized = sanitize_media_json_payload(media_json)
    decoded = json.loads(sanitized)

    assert decoded["post_url"] == "https://t.me/wakedivision/519"
    assert decoded["thumbnail_url"] == "https://cdn.example.com/cover.webp"
    assert decoded.get("url") != "https://t.me/wakedivision/519"
    assert extract_cover_image_url({"media_json": sanitized}) == "https://cdn.example.com/cover.webp"


def test_local_download_path_becomes_static_cover(monkeypatch):
    _set_media_export_config(monkeypatch)

    public_url = media_path_to_public_url("downloads/review_media/item-1346.jpg")

    assert public_url == "/static/downloads/review_media/item-1346.jpg"
    assert extract_cover_image_url({"media_json": {"type": "image", "path": "downloads/review_media/item-1346.jpg"}}) == public_url
    assert media_path_to_raw_feed_url(public_url) == ""
    assert extract_raw_feed_cover_image_url({"media_json": {"type": "image", "path": "downloads/review_media/item-1346.jpg"}}) == ""


def test_extract_cover_from_multiline_media_uses_first_valid_ref():
    media = (
        "https://mywave.ru/static/uploads/review_media/cover.jpg\n"
        "/static/downloads/review_media/local.jpg"
    )

    assert extract_raw_feed_cover_image_url({"images": media}) == "https://mywave.ru/static/uploads/review_media/cover.jpg"


def test_build_ingest_row_does_not_export_telegram_post_as_cover():
    row = build_ingest_row(
        1348,
        {
            "source": "telegram:Wake Division",
            "title": "Example",
            "content": "Body",
            "link": "https://t.me/wakedivision/519",
            "images": "https://t.me/wakedivision/519",
            "status": "new",
            "checksum": "cs-1348",
        },
    )

    assert row["source_url"] == "https://t.me/wakedivision/519"
    assert row["raw_media"] == ""
    assert row["cover_image_url"] == ""
    assert row["source_media_url"] == "https://t.me/wakedivision/519"
    assert row["media_status"] == "unsupported"
    assert row["media_error"] == "source_media_is_telegram_page_url"


def test_build_ingest_row_drops_static_cover_without_public_media_base(monkeypatch):
    _set_media_export_config(monkeypatch)

    row = build_ingest_row(
        1348,
        {
            "source": "telegram:Wake Division",
            "title": "Example",
            "content": "Body",
            "link": "https://t.me/wakedivision/519",
            "images": "/static/downloads/review_media/item-1348.jpg",
            "status": "new",
            "checksum": "cs-1348",
        },
    )

    assert row["raw_media"] == ""
    assert row["cover_image_url"] == ""


def test_build_ingest_row_exports_absolute_static_cover_when_public_media_base(monkeypatch):
    _set_media_export_config(monkeypatch, base_url="https://media.mywave.example")

    row = build_ingest_row(
        1348,
        {
            "source": "telegram:Wake Division",
            "title": "Example",
            "content": "Body",
            "link": "https://t.me/wakedivision/519",
            "images": "/static/downloads/review_media/item-1348.jpg",
            "status": "new",
            "checksum": "cs-1348",
        },
    )

    expected = "https://media.mywave.example/static/downloads/review_media/item-1348.jpg"
    assert row["raw_media"] == f'["{expected}"]'
    assert row["cover_image_url"] == expected
    assert row["source_media_url"] == "/static/downloads/review_media/item-1348.jpg"
    assert row["media_status"] == "image_ready"


def test_build_ingest_row_exports_uploaded_static_cover_via_site_base_url(monkeypatch):
    _set_media_export_config(monkeypatch, site_base_url="http://127.0.0.1:5000")

    row = build_ingest_row(
        1375,
        {
            "source": "telegram:RUWF",
            "title": "Video item",
            "content": "Body",
            "link": "https://t.me/example/1",
            "images": "/static/uploads/review_media/cover.jpg",
            "videos": "/static/uploads/review_media/clip.mp4",
            "status": "published",
            "checksum": "cs-1375",
        },
    )

    expected_image = "http://127.0.0.1:5000/static/uploads/review_media/cover.jpg"
    expected_video = "http://127.0.0.1:5000/static/uploads/review_media/clip.mp4"
    assert expected_image in row["raw_media"]
    assert expected_video in row["raw_media"]
    assert row["cover_image_url"] == expected_image
    assert row["image_url"] == expected_image
    assert row["video_url"] == expected_video
    assert row["poster_url"] == expected_image
    assert row["thumbnail_url"] == expected_image
    assert row["video_preview_image_url"] == expected_image
    assert row["media_status"] == "video_ready"


def test_sync_media_fields_media_json_includes_video_and_poster(monkeypatch):
    _set_media_export_config(monkeypatch, site_base_url="http://127.0.0.1:5000")

    from services.raw_feed_sync import _media_json_from_item

    payload = _media_json_from_item(
        {
            "videos": "/static/uploads/review_media/IMG_6439.MOV",
        },
        "http://127.0.0.1:5000/static/uploads/review_media/photo_2026-04-23_12-22-57.jpg",
    )
    decoded = json.loads(payload)

    assert decoded[0]["type"] == "image"
    assert decoded[1]["type"] == "video"
    assert decoded[1]["video_url"] == "http://127.0.0.1:5000/static/uploads/review_media/IMG_6439.MOV"
    assert decoded[1]["poster_url"] == "http://127.0.0.1:5000/static/uploads/review_media/photo_2026-04-23_12-22-57.jpg"
    assert decoded[1]["thumbnail_url"] == "http://127.0.0.1:5000/static/uploads/review_media/photo_2026-04-23_12-22-57.jpg"


def test_normalize_media_contract_fields_clears_local_video_aliases(monkeypatch):
    _set_media_export_config(monkeypatch, site_base_url="http://127.0.0.1:5000")

    normalized = normalize_media_contract_fields(
        {
            "cover_image_url": "/static/downloads/review_media/local.jpg",
            "video_url": "/static/downloads/review_media/local.mp4",
            "embed_url": "http://127.0.0.1:5000/static/downloads/review_media/local.mp4",
            "video_embed_url": "/static/downloads/review_media/local.mp4",
            "poster_url": "/static/downloads/review_media/local.jpg",
            "thumbnail_url": "/static/downloads/review_media/local.jpg",
            "video_preview_image_url": "/static/downloads/review_media/local.jpg",
        }
    )

    assert normalized["cover_image_url"] == ""
    assert normalized["video_url"] == ""
    assert normalized["embed_url"] == ""
    assert normalized["video_embed_url"] == ""
    assert normalized["poster_url"] == ""
    assert normalized["thumbnail_url"] == ""
    assert normalized["video_preview_image_url"] == ""


def test_raw_feed_validation_rejects_telegram_media_urls():
    ok, reason = validate_raw_row(
        {
            "raw_title": "Example",
            "cover_image_url": "https://t.me/wakedivision/519",
        },
        strict=False,
    )

    assert ok is False
    assert "Telegram page URL" in reason


def test_raw_feed_validation_rejects_local_media_urls(monkeypatch):
    _set_media_export_config(monkeypatch)

    ok, reason = validate_raw_row(
        {
            "raw_title": "Example",
            "cover_image_url": "/static/downloads/review_media/item-1.jpg",
        },
        strict=False,
    )

    assert ok is False
    assert "non-public media URL" in reason


def test_media_diagnostic_marks_external_cover_ok(monkeypatch):
    _set_media_export_config(monkeypatch)

    diagnostic = build_media_contract_diagnostic(
        {
            "cover_image_url": "https://cdn.example.com/news/cover.jpg",
            "raw_media": "https://source.example.com/news/cover.jpg",
        }
    )

    assert diagnostic.media_status == "image_ready"
    assert diagnostic.media_error == ""
    assert diagnostic.source_media_url == "https://source.example.com/news/cover.jpg"
    assert media_contract_is_publishable(diagnostic.as_fields()) is True


def test_media_diagnostic_marks_empty_media_missing(monkeypatch):
    _set_media_export_config(monkeypatch)

    diagnostic = build_media_contract_diagnostic({})

    assert diagnostic.media_status == "missing"
    assert diagnostic.media_error == "no_media_found"
    assert media_contract_is_publishable(diagnostic.as_fields()) is True


def test_media_diagnostic_marks_local_media_without_public_url_failed(monkeypatch, tmp_path):
    _set_media_export_config(monkeypatch)
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")

    diagnostic = build_media_contract_diagnostic({"images": str(image)})

    assert diagnostic.cover_image_path == str(image)
    assert diagnostic.media_status == "failed"
    assert diagnostic.media_error == "local_media_without_public_url"
    assert media_contract_is_publishable(diagnostic.as_fields()) is False


def test_source_media_prefers_original_over_final_cover():
    item = {
        "cover_image_url": "https://mywave.ru/static/news-media/cover.webp",
        "image_url": "https://mywave.ru/static/news-media/cover.webp",
        "images": "https://mywave.ru/static/news-media/cover.webp\ndownloads/local-cover.jpg",
    }

    assert extract_source_media_url(item) == "downloads/local-cover.jpg"
