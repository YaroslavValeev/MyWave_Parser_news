from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_bot.views import (
    _collect_review_media,
    _is_telegram_preview_candidate,
    _parse_telegram_review_link,
    _pick_review_media,
    _send_review_media_preview,
)


def test_pick_review_media_ignores_telegram_permalink_in_images():
    item = {
        "images": "https://t.me/wakediary/1878",
        "videos": None,
    }
    assert _pick_review_media(item) == (None, None)


def test_pick_review_media_prefers_direct_http_image():
    item = {
        "images": "https://example.com/media/cover.jpg",
        "videos": None,
    }
    assert _pick_review_media(item) == ("photo", "https://example.com/media/cover.jpg")


def test_pick_review_media_skips_localhost_http_and_uses_local_static(tmp_path, monkeypatch):
    photo = tmp_path / "downloads" / "photo.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"fake-jpeg")
    monkeypatch.chdir(tmp_path)
    item = {
        "images": "http://127.0.0.1:5000/static/uploads/review_media/cover.jpg\n/static/downloads/photo.jpg",
        "videos": None,
    }
    assert _pick_review_media(item) == ("photo", "/static/downloads/photo.jpg")


def test_collect_review_media_returns_album_items_in_order(tmp_path, monkeypatch):
    photo1 = tmp_path / "downloads" / "1.jpg"
    photo2 = tmp_path / "downloads" / "2.jpg"
    photo1.parent.mkdir(parents=True)
    photo1.write_bytes(b"fake-jpeg-1")
    photo2.write_bytes(b"fake-jpeg-2")
    monkeypatch.chdir(tmp_path)
    item = {
        "images": (
            "http://127.0.0.1:5000/static/uploads/review_media/cover.jpg\n"
            "/static/downloads/1.jpg\n"
            "/static/downloads/2.jpg"
        ),
        "videos": None,
    }
    assert _collect_review_media(item) == [
        ("photo", "/static/downloads/1.jpg"),
        ("photo", "/static/downloads/2.jpg"),
    ]


@pytest.mark.asyncio
async def test_send_review_media_preview_uses_media_group_for_album(tmp_path, monkeypatch):
    photo1 = tmp_path / "downloads" / "1.jpg"
    photo2 = tmp_path / "downloads" / "2.jpg"
    photo1.parent.mkdir(parents=True)
    photo1.write_bytes(b"fake-jpeg-1")
    photo2.write_bytes(b"fake-jpeg-2")
    monkeypatch.chdir(tmp_path)
    item = {
        "images": "/static/downloads/1.jpg\n/static/downloads/2.jpg",
        "videos": None,
        "link": "https://example.com/post",
    }
    message = MagicMock()
    message.answer_media_group = AsyncMock()
    message.answer_photo = AsyncMock()
    message.answer_video = AsyncMock()
    message.answer = AsyncMock()

    await _send_review_media_preview(message, item)

    message.answer_media_group.assert_awaited_once()
    message.answer_photo.assert_not_called()
    media = message.answer_media_group.await_args.args[0]
    assert len(media) == 2


def test_parse_telegram_review_link_for_public_channel():
    item = {"link": "https://t.me/wakediary/1878"}
    assert _parse_telegram_review_link(item) == ("wakediary", 1878)


def test_parse_telegram_review_link_for_c_link():
    item = {"link": "https://t.me/c/1234567890/55"}
    assert _parse_telegram_review_link(item) == (-1001234567890, 55)


def test_is_telegram_preview_candidate_detects_post_link():
    item = {"link": "https://t.me/wakediary/1878"}
    assert _is_telegram_preview_candidate(item) is True
