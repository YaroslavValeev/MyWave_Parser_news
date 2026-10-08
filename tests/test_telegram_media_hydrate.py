"""Парсинг ссылок t.me для hydrate медиа перед upload на сайт."""

import asyncio
from types import SimpleNamespace

import services.telegram_media_hydrate as hydrate_mod
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


class _Video:
    """Имитация MessageMediaDocument (не WebPage)."""


def _msg(msg_id, text="", grouped_id=None, media=None):
    return SimpleNamespace(
        id=msg_id,
        text=text,
        grouped_id=grouped_id,
        media=media,
        photo=None,
        video=media,
        document=None,
    )


def test_album_part_without_caption_gets_caption_and_video(tmp_path, monkeypatch):
    """Пост moscow_wakesurfing/21345: часть альбома без подписи → берём подпись и видео у соседей."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hydrate_mod.config, "TELEGRAM_API_ID_USER", 1, raising=False)
    monkeypatch.setattr(hydrate_mod.config, "TELEGRAM_API_HASH_USER", "x", raising=False)

    target = _msg(21345, grouped_id=7)
    siblings = [
        _msg(21344, text="Улетаю на чемпионат мира", grouped_id=7, media=_Video()),
        target,
        _msg(21350, text="чужой пост", grouped_id=None),
    ]

    class FakeClient:
        async def get_entity(self, ref):
            return ref

        async def get_messages(self, entity, ids):
            if isinstance(ids, int):
                return target
            return [m for m in siblings if m.id in ids]

    class FakeSession:
        def __init__(self, *args):
            pass

        async def get_client(self):
            return FakeClient()

        async def close_client(self):
            return None

    async def fake_download(message):
        path = tmp_path / "downloads" / f"{message.id}.mp4"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"video")
        return True

    import utils.telegram_session as session_mod

    monkeypatch.setattr(session_mod, "TelegramSessionManager", FakeSession)
    monkeypatch.setattr(hydrate_mod, "download_media_helper", fake_download)

    out = asyncio.run(
        hydrate_mod.hydrate_item_media_from_telegram(
            {"id": 5, "link": "https://t.me/moscow_wakesurfing/21345", "content": ""}
        )
    )

    assert out["content"] == "Улетаю на чемпионат мира"
    assert out["videos"] == "downloads/21344.mp4"
