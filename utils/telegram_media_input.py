"""Отправка медиа в Telegram файлом, когда Bot API не может забрать URL сам.

Серверы Telegram не всегда достают картинки с mywavewake.ru и части источников
(гео-ограничения, анти-бот, TLS). Parser их скачивает сам и шлёт как файл.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from urllib.parse import urlparse

from utils.safe_http import safe_get

LOGGER = logging.getLogger(__name__)

PHOTO_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 50 * 1024 * 1024

try:  # pragma: no cover - aiogram отсутствует в части тестовых окружений
    from aiogram.types import BufferedInputFile
except Exception:  # noqa: BLE001
    BufferedInputFile = None  # type: ignore[assignment]


def _download(url: str, max_bytes: int, timeout: float) -> tuple[bytes, str] | None:
    try:
        resp = safe_get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": "MyWaveParserBot/1.0"},
        )
    except Exception as exc:  # noqa: BLE001 - UnsafeURLError, сеть, TLS
        LOGGER.warning("tg media download failed host=%s error=%s", urlparse(url).netloc, type(exc).__name__)
        return None
    try:
        if resp.status_code != 200:
            LOGGER.warning("tg media download status=%s host=%s", resp.status_code, urlparse(url).netloc)
            return None
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                LOGGER.warning("tg media too large host=%s limit=%s", urlparse(url).netloc, max_bytes)
                return None
            chunks.append(chunk)
    finally:
        resp.close()
    if not chunks:
        return None
    name = PurePosixPath(urlparse(url).path).name or "media"
    return b"".join(chunks), name


async def http_url_as_input_file(
    url: str,
    *,
    max_bytes: int = PHOTO_MAX_BYTES,
    timeout: float = 20.0,
):
    """Скачать http(s)-медиа и вернуть BufferedInputFile или None."""
    if BufferedInputFile is None or not str(url or "").startswith(("http://", "https://")):
        return None
    result = await asyncio.to_thread(_download, url, max_bytes, timeout)
    if result is None:
        return None
    data, name = result
    return BufferedInputFile(data, filename=name)
