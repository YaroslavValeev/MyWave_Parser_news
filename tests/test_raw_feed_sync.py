import pytest

from services import raw_feed_sync


@pytest.mark.asyncio
async def test_sync_publication_queue_appends_missing_raw_feed_row(monkeypatch):
    item = {
        "id": 501,
        "checksum": "cs-501",
        "status": "approved",
        "title": "Queued item",
        "content": "Body",
    }
    nlp = {"summary": "Summary"}
    calls = {"update": [], "append": 0, "invalidate": []}

    async def fake_get_doc():
        return object()

    async def fake_prepare(prepared_item):
        return {
            **prepared_item,
            "id": 501,
            "checksum": "cs-501",
            "raw_media": "",
            "media_json": "",
            "cover_image_url": "",
            "image_url": "",
            "video_url": "",
            "embed_url": "",
            "video_embed_url": "",
            "poster_url": "",
            "thumbnail_url": "",
            "video_preview_image_url": "",
        }

    async def fake_update_item(doc, sheet_name, payload, lookup_field="checksum"):
        calls["update"].append((lookup_field, dict(payload)))
        return len(calls["update"]) >= 3

    async def fake_append_rows(doc, rows):
        calls["append"] += 1
        assert rows[0]["id"] == "501"
        assert rows[0]["status"] == "READY_TO_PUBLISH"
        return 1

    async def fake_invalidate(**kwargs):
        calls["invalidate"].append(kwargs)
        return True

    monkeypatch.setattr(raw_feed_sync, "_get_doc", fake_get_doc)
    monkeypatch.setattr(raw_feed_sync, "_prepare_publishable_item_media", fake_prepare)
    monkeypatch.setattr(raw_feed_sync, "update_item", fake_update_item)
    monkeypatch.setattr(raw_feed_sync, "append_raw_feed_rows", fake_append_rows)
    monkeypatch.setattr(raw_feed_sync, "invalidate_site_blog_cache", fake_invalidate)

    result = await raw_feed_sync.sync_publication_queue(item, nlp)

    assert result is True
    assert calls["append"] == 1
    assert calls["update"][0][0] == "checksum"
    assert calls["update"][1][0] == "id"
    assert calls["update"][2][0] == "checksum"
    assert calls["invalidate"][0]["reason"] == "raw_feed_ready_to_publish"


@pytest.mark.asyncio
async def test_sync_publication_result_appends_missing_raw_feed_row(monkeypatch):
    item = {
        "id": 777,
        "checksum": "cs-777",
        "status": "published",
        "title": "Published item",
        "content": "Body",
    }
    nlp = {"summary": "Summary"}
    calls = {"update": [], "append": 0, "invalidate": []}

    async def fake_get_doc():
        return object()

    async def fake_prepare(prepared_item):
        return {
            **prepared_item,
            "id": 777,
            "checksum": "cs-777",
            "raw_media": "",
            "media_json": "",
            "cover_image_url": "",
            "image_url": "",
            "video_url": "",
            "embed_url": "",
            "video_embed_url": "",
            "poster_url": "",
            "thumbnail_url": "",
            "video_preview_image_url": "",
        }

    async def fake_update_item(doc, sheet_name, payload, lookup_field="checksum"):
        calls["update"].append((lookup_field, dict(payload)))
        return len(calls["update"]) >= 3

    async def fake_append_rows(doc, rows):
        calls["append"] += 1
        assert rows[0]["id"] == "777"
        assert rows[0]["status"] == "PUBLISHED"
        return 1

    async def fake_invalidate(**kwargs):
        calls["invalidate"].append(kwargs)
        return True

    monkeypatch.setattr(raw_feed_sync, "_get_doc", fake_get_doc)
    monkeypatch.setattr(raw_feed_sync, "_prepare_publishable_item_media", fake_prepare)
    monkeypatch.setattr(raw_feed_sync, "update_item", fake_update_item)
    monkeypatch.setattr(raw_feed_sync, "append_raw_feed_rows", fake_append_rows)
    monkeypatch.setattr(raw_feed_sync, "invalidate_site_blog_cache", fake_invalidate)

    result = await raw_feed_sync.sync_publication_result(item, nlp, published=True)

    assert result is True
    assert calls["append"] == 1
    assert calls["update"][0][0] == "checksum"
    assert calls["update"][1][0] == "id"
    assert calls["update"][2][0] == "checksum"
    assert calls["invalidate"][0]["reason"] == "raw_feed_published"
