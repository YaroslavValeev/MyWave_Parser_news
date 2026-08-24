import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from services.publication import PublicationService, PublicationSendError
from storage.repository import AsyncNewsRepository


class DummyRepo(AsyncNewsRepository):
    def __init__(self):
        self._store = {}
        self._nlp = {1: {"summary": "short summary", "author_notes": "owner note"}}

    async def get_last_log(self, item_id, message):
        logs = self._store.setdefault(item_id, {}).setdefault("logs", [])
        for _level, msg, meta in reversed(logs):
            if msg == message:
                return {
                    "meta": meta or {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
        return None

    async def list_publication_candidates(self, limit=10):
        return [
            {"id": 1, "title": "T", "content": "C", "link": "http://x"}
        ]

    async def get_nlp_results(self, item_id: int):
        return self._nlp.get(item_id, {})

    async def save_nlp_results(self, item_id, **kwargs):
        row = dict(self._nlp.get(item_id, {}))
        for key, value in kwargs.items():
            if value is not None:
                row[key] = value
        self._nlp[item_id] = row

    async def update_status(self, item_id: int, status: str):
        self._store.setdefault(item_id, {})["status"] = status

    async def save_publication(self, item_id: int, channel_id: str, message_id: str):
        self._store.setdefault(item_id, {})["pub"] = (channel_id, message_id)

    async def log_event(self, item_id, level, message, meta=None):
        self._store.setdefault(item_id, {}).setdefault("logs", []).append((level, message, meta))


@pytest.mark.asyncio
async def test_publish_success():
    repo = DummyRepo()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")
    published = await svc.publish_pending(limit=1)
    assert published == 1


@pytest.mark.asyncio
async def test_publish_uses_item_image_as_cover():
    repo = DummyRepo()

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "T",
                "content": "C",
                "link": "http://x",
                "images": "https://cdn.example.com/cover.jpg",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")

    published = await svc.publish_pending(limit=1)

    assert published == 1
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs["photo"] == "https://cdn.example.com/cover.jpg"


@pytest.mark.asyncio
async def test_publish_skips_localhost_cover_and_uses_local_static_file(monkeypatch, tmp_path):
    repo = DummyRepo()
    local_file = tmp_path / "downloads" / "cover.jpg"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"fake-jpeg")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("services.publication.FSInputFile", lambda path: ("file", path))

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "T",
                "content": "C",
                "link": "http://x",
                "images": "http://127.0.0.1:5000/static/uploads/review_media/cover.jpg\n/static/downloads/cover.jpg",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")

    published = await svc.publish_pending(limit=1)

    assert published == 1
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs["photo"] == ("file", "downloads\\cover.jpg")


@pytest.mark.asyncio
async def test_publish_failure_retry():
    repo = DummyRepo()
    class BadBot:
        async def send_message(self, **kwargs):
            raise Exception("boom")

    bot = BadBot()
    svc = PublicationService(repo, bot, channel_id="42")
    # Configure low attempts to force marking error after failure
    svc._max_attempts = 1
    published = await svc.publish_pending(limit=1)
    assert published == 0


@pytest.mark.asyncio
async def test_publish_skips_stale_retry_items():
    repo = DummyRepo()

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "stale",
                "content": "C",
                "link": "http://x",
                "status": "publish_retry",
                "updated_at": "2026-04-10T00:00:00+00:00",
                "created_at": "2026-04-10T00:00:00+00:00",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(
        repo,
        bot,
        channel_id="42",
        now_func=lambda: datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    published = await svc.publish_pending(limit=1)
    assert published == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_retries_recent_retry_items():
    repo = DummyRepo()

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "recent retry",
                "content": "C",
                "link": "http://x",
                "status": "publish_retry",
                "updated_at": "2026-04-16T23:00:00+00:00",
                "created_at": "2026-04-16T23:00:00+00:00",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(
        repo,
        bot,
        channel_id="42",
        now_func=lambda: datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    published = await svc.publish_pending(limit=1)
    assert published == 1


@pytest.mark.asyncio
async def test_publish_skips_items_scheduled_in_future():
    repo = DummyRepo()

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "scheduled",
                "content": "C",
                "link": "http://x",
                "status": "ready_to_publish",
                "scheduled_at": "2026-04-17T10:00:00+00:00",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(
        repo,
        bot,
        channel_id="42",
        now_func=lambda: datetime(2026, 4, 17, 9, 0, tzinfo=timezone.utc),
    )
    published = await svc.publish_pending(limit=1)
    assert published == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_sends_items_when_scheduled_time_reached():
    repo = DummyRepo()

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "title": "scheduled due",
                "content": "C",
                "link": "http://x",
                "status": "ready_to_publish",
                "scheduled_at": "2026-04-17T10:00:00+00:00",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(
        repo,
        bot,
        channel_id="42",
        now_func=lambda: datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
    )
    published = await svc.publish_pending(limit=1)
    assert published == 1
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_blocks_item_without_owner_comment():
    repo = DummyRepo()
    repo._nlp[1] = {"summary": "short summary"}
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")
    published = await svc.publish_pending(limit=1)
    assert published == 0
    bot.send_message.assert_not_awaited()
    assert repo._store[1]["status"] == "review"
    assert any(msg == "publication_blocked_missing_author_note" for _lvl, msg, _meta in repo._store[1]["logs"])


@pytest.mark.asyncio
async def test_publish_blocks_title_only_summary_fallback_even_with_owner_comment():
    repo = DummyRepo()
    repo._nlp[1] = {
        "summary": "Кристина Колесникова — российская певица и композитор...",
        "author_notes": "Нужно проверить по источнику",
        "extra": {"sanitized_text": "Cristina Kolesnikova"},
    }

    async def list_candidates(limit=10):
        return [
            {
                "id": 1,
                "source": "ДИАЛОГИ О РЫБАЛКЕ",
                "title": "Cristina Kolesnikova",
                "content": "",
                "link": "https://t.me/talktofish/347",
                "status": "publish_retry",
            }
        ]

    repo.list_publication_candidates = list_candidates  # type: ignore[method-assign]
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    svc = PublicationService(repo, bot, channel_id="42")
    published = await svc.publish_pending(limit=1)

    assert published == 0
    bot.send_message.assert_not_awaited()
    assert repo._store[1]["status"] == "review"
    assert any(msg == "publication_blocked_untrusted_summary" for _lvl, msg, _meta in repo._store[1]["logs"])


def test_build_caption_uses_safe_title_and_avoids_title_only_summary_fallback():
    item = {
        "id": 101,
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "",
        "link": "https://t.me/talktofish/347",
    }
    nlp = {
        "summary": "Кристина Колесникова — российская певица и композитор...",
        "extra": {"sanitized_text": "Cristina Kolesnikova"},
        "author_notes": "Нужно проверить по источнику",
    }

    caption = PublicationService._build_caption(item, nlp)

    assert "Пост из ДИАЛОГИ О РЫБАЛКЕ #347" in caption
    assert "российская певица" not in caption
    assert "нет текстового контента" in caption
    assert 'href="https://t.me/talktofish/347">Источник</a>' in caption


def test_build_caption_merges_notes_without_author_block_when_no_merged_text():
    item = {
        "id": 203,
        "source": "rss:Wakeboarding Mag",
        "title": "Brisbane 2032 wake cable",
        "content": "Foreign article body",
        "link": "https://example.com/post/203",
    }
    nlp = {
        "author_notes": "Скрестили пальцы и делаем всё возможное.",
        "summary": "Материал о вейкбординге.",
    }

    caption = PublicationService._build_caption(item, nlp)

    assert "<b>Мнение автора</b>" not in caption
    assert "Скрестили пальцы" in caption


def test_build_caption_uses_merged_text_as_ready_post_without_owner_meta_block():
    item = {
        "id": 202,
        "source": "telegram:chan",
        "title": "Оригинальный заголовок",
        "content": "Исходный текст",
        "link": "https://example.com/post/202",
    }
    nlp = {
        "merged_text": "Собрал для себя главное по этой новости: старт сильный, а для нас это хороший ориентир на сезон.",
    }

    caption = PublicationService._build_caption(item, nlp)

    assert "Собрал для себя главное по этой новости" in caption
    assert "Оригинальный заголовок" not in caption
    assert "<b>Мнение автора</b>" not in caption
    assert 'href="https://example.com/post/202">Источник</a>' in caption
    assert ">сайт</a>" in caption
    assert ">тг-админ</a>" in caption


def test_build_caption_prefers_summary_and_raw_owner_notes():
    item = {
        "id": 204,
        "source": "telegram:chan",
        "title": "Новость дня",
        "content": "Длинный оригинал",
        "link": "https://example.com/post/204",
    }
    nlp = {
        "summary": "Короткое саммари.",
        "author_notes": "Мой комментарий почти как есть!!!",
        "merged_text": "Старый rewrite не должен побеждать",
    }
    caption = PublicationService._build_caption(item, nlp)
    assert "Короткое саммари." in caption
    assert "Мой комментарий почти как есть!!!" in caption
    assert "Старый rewrite" not in caption
    assert 'href="https://example.com/post/204">Источник</a>' in caption
    assert ">сайт</a>" in caption
