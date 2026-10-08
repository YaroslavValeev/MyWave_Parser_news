import pytest

from config.settings import config
from services import media_pipeline
from services.site_media_client import MediaUploadResult


class FakeRepo:
    def __init__(self, items):
        self.items = {int(i["id"]): dict(i) for i in items}
        self.logs: list[tuple[int, str, dict]] = []

    async def list_items_by_status(self, statuses, *, limit=100, order="ASC"):
        return [dict(i) for i in self.items.values()][:limit]

    async def get_item(self, item_id):
        return dict(self.items.get(item_id) or {})

    async def get_last_log(self, item_id, message):
        for iid, msg, meta in reversed(self.logs):
            if iid == item_id and msg == message:
                return {"meta": meta}
        return None

    async def log_event(self, item_id, level, message, meta=None):
        self.logs.append((item_id, message, meta or {}))


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr("services.site_media_client.media_upload_configured", lambda: True)
    monkeypatch.setattr(config, "MEDIA_HYDRATE_MAX_ATTEMPTS", 2)


@pytest.mark.asyncio
async def test_pipeline_processes_once_and_marks_done(monkeypatch):
    repo = FakeRepo([{"id": 1, "images": ""}])
    calls = []

    async def fake_autoupload(r, item_id, **kwargs):
        calls.append(item_id)
        r.items[item_id]["images"] = "https://mywavewake.ru/static/uploads/review_media/1.jpg\ndownloads/1.jpg"
        return MediaUploadResult(ok=True, url="https://mywavewake.ru/static/uploads/review_media/1.jpg")

    monkeypatch.setattr(
        "services.site_media_client.maybe_autoupload_local_cover_and_sync_sheet", fake_autoupload
    )

    assert await media_pipeline.run_media_hydrate(repo) == 1
    assert await media_pipeline.run_media_hydrate(repo) == 0
    assert calls == [1]
    done = await repo.get_last_log(1, media_pipeline.DONE_LOG)
    assert done["meta"]["has_media"] is True and done["meta"]["uploaded"] is True


@pytest.mark.asyncio
async def test_pipeline_retries_failed_upload_until_limit(monkeypatch):
    repo = FakeRepo([{"id": 2, "images": "downloads/2.jpg"}])

    async def failing(r, item_id, **kwargs):
        return MediaUploadResult(ok=False, error="network", status_code=502)

    monkeypatch.setattr("services.site_media_client.maybe_autoupload_local_cover_and_sync_sheet", failing)

    await media_pipeline.run_media_hydrate(repo)
    assert await repo.get_last_log(2, media_pipeline.DONE_LOG) is None
    await media_pipeline.run_media_hydrate(repo)
    done = await repo.get_last_log(2, media_pipeline.DONE_LOG)
    assert done is not None and done["meta"]["upload_failed"] is True
    assert await media_pipeline.run_media_hydrate(repo) == 0


@pytest.mark.asyncio
async def test_pipeline_noop_when_upload_not_configured(monkeypatch):
    monkeypatch.setattr("services.site_media_client.media_upload_configured", lambda: False)
    repo = FakeRepo([{"id": 3}])
    assert await media_pipeline.run_media_hydrate(repo) == 0


def test_review_media_prefers_local_file(monkeypatch, tmp_path):
    from telegram_bot import views

    monkeypatch.chdir(tmp_path)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "7.jpg").write_bytes(b"jpeg")
    item = {
        "id": 7,
        "images": "https://mywavewake.ru/static/uploads/review_media/7.jpg\ndownloads/7.jpg",
    }
    media = views._collect_review_media(item)
    assert media == [("photo", "downloads/7.jpg")]
