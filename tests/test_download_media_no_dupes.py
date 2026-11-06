"""download_media: skip existing / overwrite empty, never create «file (1).ext»."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.helpers import download_media


@pytest.mark.asyncio
async def test_download_media_skips_existing_nonempty(tmp_path, monkeypatch):
    target = tmp_path / "42.jpg"
    target.write_bytes(b"already-here")

    message = SimpleNamespace(
        id=42,
        media=object(),
        photo=object(),
        video=None,
        document=None,
        download_media=AsyncMock(),
    )
    monkeypatch.setattr("utils.helpers.config.MEDIA_DOWNLOAD_DELAY", 0)

    ok = await download_media(message, download_dir=str(tmp_path) + "/")
    assert ok is True
    message.download_media.assert_not_awaited()
    assert list(tmp_path.glob("* (1).*")) == []
    assert target.read_bytes() == b"already-here"


@pytest.mark.asyncio
async def test_download_media_overwrites_empty(tmp_path, monkeypatch):
    target = tmp_path / "7.jpg"
    target.write_bytes(b"")

    async def _fake_download(*, file):
        Path = __import__("pathlib").Path
        Path(file).write_bytes(b"new-bytes")
        return file

    message = SimpleNamespace(
        id=7,
        media=object(),
        photo=object(),
        video=None,
        document=None,
        download_media=AsyncMock(side_effect=_fake_download),
    )
    monkeypatch.setattr("utils.helpers.config.MEDIA_DOWNLOAD_DELAY", 0)

    ok = await download_media(message, download_dir=str(tmp_path) + "/")
    assert ok is True
    message.download_media.assert_awaited_once()
    assert (tmp_path / "7.jpg").read_bytes() == b"new-bytes"
    assert list(tmp_path.glob("* (1).*")) == []


@pytest.mark.asyncio
async def test_fetch_items_accepts_download_media_kwarg():
    """Регрессия: scheduler передаёт download_media=… в _fetch_items."""
    import inspect

    from services.manual_collect import _fetch_items

    params = inspect.signature(_fetch_items).parameters
    assert "download_media" in params
