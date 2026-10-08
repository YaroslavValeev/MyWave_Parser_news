"""Скачать вложение Telegram на диск для publish → site upload.

Нужен, когда полный /parse шёл с TELEGRAM_SKIP_MEDIA_FULL_COLLECT=true
и в item нет локального файла, а только t.me / msg_id.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from config.settings import config
from utils.helpers import _media_ext_for_message, download_media as download_media_helper
from utils.media_utils import VIDEO_EXTENSIONS, is_telegram_url

LOGGER = logging.getLogger(__name__)

# Альбом до 10 элементов → соседи в пределах ±10 id.
_ALBUM_WINDOW = 10

_TG_POST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/(?:s/)?(?P<user>[A-Za-z0-9_]+)/(?P<msg>\d+)",
    re.IGNORECASE,
)
_TG_PRIVATE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/c/(?P<chat>\d+)/(?P<msg>\d+)",
    re.IGNORECASE,
)


def _parse_telegram_post(item: Mapping[str, Any]) -> tuple[str | int | None, int | None]:
    """Вернуть (entity_ref, message_id) для Telethon get_entity / get_messages."""
    candidates: list[str] = []
    for key in ("link", "canonical_url", "source_url"):
        value = str(item.get(key) or "").strip()
        if value:
            candidates.append(value)
    media_json = item.get("media_json")
    if isinstance(media_json, str) and "post_url" in media_json:
        candidates.append(media_json)
    elif isinstance(media_json, Mapping):
        post = str(media_json.get("post_url") or "").strip()
        if post:
            candidates.append(post)

    for raw in candidates:
        m = _TG_PRIVATE_RE.search(raw)
        if m:
            # Private channel: Telethon peer id = -100{chat_id}
            chat_id = int(m.group("chat"))
            return int(f"-100{chat_id}"), int(m.group("msg"))
        m = _TG_POST_RE.search(raw)
        if m:
            return m.group("user"), int(m.group("msg"))

    msg_raw = str(item.get("source_item_id") or item.get("id") or "").strip()
    msg_id: int | None = None
    if msg_raw.isdigit():
        msg_id = int(msg_raw)
    debug = str(item.get("debug_info") or "")
    if msg_id is None:
        m = re.search(r"msg_id=(\d+)", debug)
        if m:
            msg_id = int(m.group(1))
    if msg_id is None:
        return None, None

    source_url = str(item.get("source_url") or "").strip()
    if not source_url:
        return None, None
    if is_telegram_url(source_url) or "t.me" in source_url.lower():
        parsed = urlparse(source_url if "://" in source_url else f"https://{source_url}")
        path = (parsed.path or "").strip("/")
        user = path.split("/")[0] if path else ""
        if user and user != "c":
            return user, msg_id
    # username / @channel / channel name as source_url
    user = source_url.rstrip("/").split("/")[-1].lstrip("@")
    if user:
        return user, msg_id
    return None, None


def _existing_download(msg_id: int) -> Path | None:
    downloads = Path("downloads")
    if not downloads.is_dir():
        return None
    for path in downloads.glob(f"{msg_id}.*"):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


async def hydrate_item_media_from_telegram(item: Mapping[str, Any]) -> dict[str, Any]:
    """Если локальных файлов нет — скачать вложение поста в downloads/ и проставить images/videos."""
    out = dict(item)
    entity_ref, msg_id = _parse_telegram_post(out)
    if entity_ref is None or msg_id is None:
        return out

    existing = _existing_download(msg_id)
    files: list[Path] = [existing] if existing is not None else []
    need_caption = not str(out.get("content") or "").strip()
    if existing is None or need_caption:
        if not config.TELEGRAM_API_ID_USER or not config.TELEGRAM_API_HASH_USER:
            LOGGER.warning(
                "telegram media hydrate skipped: Telethon credentials missing item_id=%s",
                out.get("id"),
            )
            return out
        from utils.telegram_session import TelegramSessionManager

        session_manager = TelegramSessionManager(
            config.TELEGRAM_API_ID_USER,
            config.TELEGRAM_API_HASH_USER,
            config.TELEGRAM_PHONE,
        )
        client = await session_manager.get_client()
        if client is None:
            LOGGER.warning(
                "telegram media hydrate failed: client None item_id=%s msg_id=%s",
                out.get("id"),
                msg_id,
            )
            return out
        try:
            entity = await client.get_entity(entity_ref)
            message = await client.get_messages(entity, ids=msg_id)
            if not message:
                LOGGER.info(
                    "telegram media hydrate: message not found msg_id=%s item_id=%s",
                    msg_id,
                    out.get("id"),
                )
                return out
            group = await _album_messages(client, entity, message)
            if need_caption:
                caption = next(
                    (str(m.text).strip() for m in group if str(getattr(m, "text", "") or "").strip()),
                    "",
                )
                if caption:
                    out["content"] = caption
                    LOGGER.info(
                        "telegram caption hydrated item_id=%s msg_id=%s len=%s",
                        out.get("id"),
                        msg_id,
                        len(caption),
                    )
            if not files:
                files = await _download_album_media(group)
            if not files:
                LOGGER.info(
                    "telegram media hydrate: no media msg_id=%s item_id=%s album=%s",
                    msg_id,
                    out.get("id"),
                    len(group),
                )
                return out
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "telegram media hydrate error item_id=%s msg_id=%s",
                out.get("id"),
                msg_id,
            )
            return out
        finally:
            await session_manager.close_client()

    for path in reversed(files):
        _apply_local_file(out, path)
    LOGGER.info(
        "telegram media hydrated item_id=%s paths=%s",
        out.get("id"),
        ",".join(p.as_posix() for p in files),
    )
    return out


def _apply_local_file(out: dict[str, Any], path: Path) -> None:
    ref = path.as_posix()
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        prev = str(out.get("videos") or "").strip()
        lines = [part for part in prev.splitlines() if part.strip()]
        if ref not in lines:
            lines.insert(0, ref)
        out["videos"] = "\n".join(lines)
        return
    prev = str(out.get("images") or "").strip()
    lines = [part for part in prev.splitlines() if part.strip()]
    if ref not in lines:
        lines.insert(0, ref)
    out["images"] = "\n".join(lines)
    # Не оставляем t.me в cover — сайт его не скачает.
    cover = str(out.get("cover_image_url") or "").strip()
    if not cover or is_telegram_url(cover):
        out["cover_image_url"] = ref


async def _album_messages(client: Any, entity: Any, message: Any) -> list[Any]:
    """Сообщение + остальные части альбома (тот же grouped_id, соседние id)."""
    grouped_id = getattr(message, "grouped_id", None)
    if not grouped_id:
        return [message]
    ids = [i for i in range(message.id - _ALBUM_WINDOW, message.id + _ALBUM_WINDOW + 1) if i > 0]
    neighbours = await client.get_messages(entity, ids=ids)
    group = [
        m for m in (neighbours or [])
        if m is not None and getattr(m, "grouped_id", None) == grouped_id
    ]
    group.sort(key=lambda m: m.id)
    return group or [message]


async def _download_album_media(group: list[Any]) -> list[Path]:
    """Скачать первое видео и первое фото альбома (обложка + ролик)."""
    files: list[Path] = []
    kinds_done: set[str] = set()
    for msg in group:
        if not getattr(msg, "media", None) or type(msg.media).__name__ == "MessageMediaWebPage":
            continue
        ext = _media_ext_for_message(msg)
        kind = "video" if ext in VIDEO_EXTENSIONS else "image"
        if kind in kinds_done or ext == ".bin":
            continue
        ok = await download_media_helper(msg)
        path = Path("downloads") / f"{msg.id}{ext}"
        if not ok or not path.is_file() or path.stat().st_size <= 0:
            LOGGER.warning("telegram media hydrate download failed msg_id=%s kind=%s", msg.id, kind)
            continue
        files.append(path)
        kinds_done.add(kind)
        if len(kinds_done) == 2:
            break
    # Фото первым: из него берётся обложка.
    files.sort(key=lambda p: p.suffix.lower() in VIDEO_EXTENSIONS)
    return files


__all__ = ["hydrate_item_media_from_telegram", "_parse_telegram_post"]
