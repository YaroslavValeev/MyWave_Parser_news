from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config.settings import config
from services.media_upload import (
    MediaUploadResult,
    maybe_autoupload_local_cover_and_sync_sheet,
    media_upload_configured,
    media_upload_url,
    prepare_item_media_for_raw_feed,
    upload_cover_image,
)


class _Response:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _configure_upload(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_UPLOAD_URL", "")
    monkeypatch.setattr(config, "SITE_BASE_URL", "https://mywave.ru")
    monkeypatch.setattr(config, "PUBLIC_MEDIA_BASE_URL", "")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_ENDPOINT", "/api/blog/media/upload")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_TOKEN", "secret-token")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(config, "MEDIA_UPLOAD_MAX_BYTES", 10 * 1024 * 1024)


def test_media_upload_url_uses_site_base(monkeypatch):
    _configure_upload(monkeypatch)

    assert media_upload_configured() is True
    assert media_upload_url() == "https://mywave.ru/api/blog/media/upload"


def test_media_upload_url_prefers_full_media_upload_url(monkeypatch):
    _configure_upload(monkeypatch)
    monkeypatch.setattr(config, "MEDIA_UPLOAD_URL", "https://mywavetreaning.ru/api/media/upload")

    assert media_upload_url() == "https://mywavetreaning.ru/api/media/upload"


@pytest.mark.asyncio
async def test_upload_cover_image_posts_multipart(monkeypatch, tmp_path):
    _configure_upload(monkeypatch)
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"fake-jpeg-body")
    calls = {}

    def fake_post(url, *, headers, data, files, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["data"] = data
        calls["file_name"] = files["file"][0]
        calls["file_mime"] = files["file"][2]
        calls["timeout"] = timeout
        return _Response(
            201,
            {
                "ok": True,
                "url": "https://mywave.ru/static/news-media/2026/04/parser-1.webp",
                "mime_type": "image/webp",
                "bytes": 123,
                "checksum": "site-checksum",
            },
        )

    monkeypatch.setattr("services.site_media_client.requests.post", fake_post)

    result = await upload_cover_image(
        image,
        item_id=1,
        item={"link": "https://t.me/source/1", "source": "telegram:source"},
    )

    assert result.ok is True
    assert result.url == "https://mywave.ru/static/news-media/2026/04/parser-1.webp"
    assert calls["url"] == "https://mywave.ru/api/blog/media/upload"
    assert calls["headers"]["Authorization"] == "Bearer secret-token"
    assert calls["headers"]["X-Media-Upload-Token"] == "secret-token"
    assert calls["headers"]["X-Idempotency-Key"]
    assert len(calls["headers"]["X-Idempotency-Key"]) == 64
    assert calls["data"]["item_id"] == "1"
    assert calls["data"]["source_url"] == "https://t.me/source/1"
    assert calls["data"]["source_name"] == "telegram:source"
    assert calls["file_name"] == "cover.jpg"
    assert calls["file_mime"] == "image/jpeg"
    assert calls["timeout"] == 15


@pytest.mark.asyncio
async def test_upload_cover_image_accepts_public_url_response(monkeypatch, tmp_path):
    _configure_upload(monkeypatch)
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"fake-jpeg-body")

    def fake_post(url, *, headers, data, files, timeout):
        return _Response(
            201,
            {
                "ok": True,
                "public_url": "https://mywave.ru/static/uploads/review_media/cover.jpg",
            },
        )

    monkeypatch.setattr("services.site_media_client.requests.post", fake_post)

    result = await upload_cover_image(image, item_id=1, item={})

    assert result.ok is True
    assert result.url == "https://mywave.ru/static/uploads/review_media/cover.jpg"


@pytest.mark.asyncio
async def test_prepare_item_media_for_raw_feed_uploads_local_cover(monkeypatch, tmp_path):
    _configure_upload(monkeypatch)
    image = tmp_path / "cover.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-png-body")

    def fake_post(url, *, headers, data, files, timeout):
        return _Response(
            200,
            {"ok": True, "url": "https://mywave.ru/static/news-media/2026/04/parser-2.webp"},
        )

    monkeypatch.setattr("services.site_media_client.requests.post", fake_post)

    item, result = await prepare_item_media_for_raw_feed(
        2,
        {
            "id": 2,
            "source": "telegram:source",
            "link": "https://t.me/source/2",
            "images": str(image),
        },
    )

    assert result is not None and result.ok is True
    assert item["cover_image_url"] == "https://mywave.ru/static/news-media/2026/04/parser-2.webp"
    assert item["image_url"] == "https://mywave.ru/static/news-media/2026/04/parser-2.webp"
    assert item["images"].splitlines()[0] == "https://mywave.ru/static/news-media/2026/04/parser-2.webp"


@pytest.mark.asyncio
async def test_prepare_item_media_for_raw_feed_leaves_empty_when_upload_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MEDIA_UPLOAD_URL", "")
    monkeypatch.setattr(config, "SITE_BASE_URL", "")
    monkeypatch.setattr(config, "PUBLIC_MEDIA_BASE_URL", "")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_ENDPOINT", "/api/blog/media/upload")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_TOKEN", "")
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"fake-jpeg-body")

    item, result = await prepare_item_media_for_raw_feed(3, {"images": str(image)})

    assert result is None
    assert item == {"images": str(image)}


@pytest.mark.asyncio
async def test_prepare_item_media_for_raw_feed_uploads_static_download_cover_even_with_site_base(
    monkeypatch,
    tmp_path,
):
    _configure_upload(monkeypatch)
    image = tmp_path / "downloads" / "cover.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"fake-jpeg-body")
    monkeypatch.chdir(tmp_path)

    def fake_post(url, *, headers, data, files, timeout):
        return _Response(
            201,
            {"ok": True, "public_url": "https://mywave.ru/static/uploads/review_media/cover.jpg"},
        )

    monkeypatch.setattr("services.site_media_client.requests.post", fake_post)

    item, result = await prepare_item_media_for_raw_feed(
        4,
        {
            "id": 4,
            "images": "/static/downloads/cover.jpg",
            "cover_image_url": "/static/downloads/cover.jpg",
        },
    )

    assert result is not None and result.ok is True
    assert item["cover_image_url"] == "https://mywave.ru/static/uploads/review_media/cover.jpg"
    assert item["images"].splitlines()[0] == "https://mywave.ru/static/uploads/review_media/cover.jpg"


@pytest.mark.asyncio
async def test_maybe_autoupload_returns_none_without_upload_config(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_UPLOAD_URL", "")
    monkeypatch.setattr(config, "SITE_BASE_URL", "")
    monkeypatch.setattr(config, "PUBLIC_MEDIA_BASE_URL", "")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_ENDPOINT", "/api/blog/media/upload")
    monkeypatch.setattr(config, "MEDIA_UPLOAD_TOKEN", "")

    async def boom_get_item(_):
        raise AssertionError("get_item should not be called")

    class Repo:
        get_item = boom_get_item

    assert await maybe_autoupload_local_cover_and_sync_sheet(Repo(), 1) is None


@pytest.mark.asyncio
async def test_maybe_autoupload_persists_images_and_syncs_sheet(monkeypatch):
    _configure_upload(monkeypatch)

    class FakeRepo:
        def __init__(self) -> None:
            self.item = {
                "id": 7,
                "checksum": "chk",
                "images": "downloads/local.png",
                "videos": None,
                "link": "https://example.com/p",
            }
            self.events: list[tuple[str, str]] = []

        async def get_item(self, item_id: int):
            assert item_id == 7
            return dict(self.item)

        async def update_item_media(self, item_id: int, *, images: str, videos):  # noqa: ANN001
            self.item["images"] = images

        async def log_event(self, item_id: int, level: str, message: str, meta=None):  # noqa: ANN001
            self.events.append((level, message))

    async def fake_prepare(iid: int, item):
        out = dict(item)
        out["images"] = "https://mywave.ru/pub/cover.webp\ndownloads/local.png"
        return out, MediaUploadResult(ok=True, url="https://mywave.ru/pub/cover.webp")

    monkeypatch.setattr("services.site_media_client.prepare_item_media_for_raw_feed", fake_prepare)
    monkeypatch.setattr("services.raw_feed_sync.sync_media_fields", AsyncMock(return_value=True))

    repo = FakeRepo()
    res = await maybe_autoupload_local_cover_and_sync_sheet(repo, 7, user_id=1, username="t")
    assert res is not None and res.ok is True
    assert "pub/cover.webp" in (repo.item.get("images") or "")
    assert ("info", "owner_cover_auto_uploaded") in repo.events


@pytest.mark.asyncio
async def test_maybe_autoupload_logs_on_upload_failure(monkeypatch):
    _configure_upload(monkeypatch)

    class FakeRepo:
        def __init__(self) -> None:
            self.item = {"id": 3, "checksum": "c", "images": "x.jpg", "videos": None}
            self.events: list[tuple[str, str]] = []

        async def get_item(self, item_id: int):
            return dict(self.item)

        async def log_event(self, item_id: int, level: str, message: str, meta=None):  # noqa: ANN001
            self.events.append((level, message))

    async def fake_prepare(_iid: int, item):
        return dict(item), MediaUploadResult(ok=False, error="network", status_code=500)

    monkeypatch.setattr("services.site_media_client.prepare_item_media_for_raw_feed", fake_prepare)

    repo = FakeRepo()
    res = await maybe_autoupload_local_cover_and_sync_sheet(repo, 3)
    assert res is not None and res.ok is False
    assert ("warning", "owner_cover_auto_upload_failed") in repo.events
