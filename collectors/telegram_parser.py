import asyncio
import logging
import random
import json
from datetime import datetime
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator
from utils.helpers import (
    _media_ext_for_message,
    download_media as download_media_helper,
)
from telethon import TelegramClient
from utils.media_utils import VIDEO_EXTENSIONS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseParser(ABC):
    """
    Абстрактный базовый класс для всех парсеров.
    """
    def __init__(self, limit: int = 50):
        """
        Инициализирует BaseParser.

        Args:
            limit (int): Максимальное количество элементов для парсинга (по умолчанию 50).
        """
        self.limit = limit

    @abstractmethod
    def parse(self, client: TelegramClient, source):
        """
        Abstract method for parsing data from a source.

        Args:
            client (TelegramClient): Клиент Telegram.
            source: Источник данных (объект с атрибутом url).

        """
        raise NotImplementedError

class TelethonParser(BaseParser):
    """
    Парсер для Telegram-каналов.
    """
    def __init__(self, limit: int = 50):
        """
        Инициализирует парсер Telegram.

        Args:
            limit (int): Максимальное количество сообщений для парсинга (по умолчанию 50).
        """
        super().__init__(limit)

    async def parse(
        self,
        client: TelegramClient,
        source,
        *,
        download_media: bool = True,
    ) -> Generator[dict, None, None]:  # type: ignore
        """
        Парсит сообщения из Telegram-канала и возвращает данные по структуре raw_feed.

        Args:
            client (TelegramClient): Клиент Telegram.
            source: Источник данных (объект с атрибутом url).
            download_media: скачивать вложения на диск (False — только метаданные/текст).

        Yields:
            dict: Словарь с данными сообщения.

        Raises:
            AttributeError: Если у источника отсутствует атрибут url.
            Exception: При ошибках парсинга или подключения.
        """
        if not hasattr(source, 'url') or not source.url:
            logger.error("Источник данных не содержит атрибут url или url пуст.")
            raise AttributeError("Источник данных должен содержать атрибут url")

        entries: list[dict] = []
        try:
            await self.human_delay()  # Случайная задержка перед началом
            entity = await client.get_entity(source.url)
            logger.info(f"Подключение к каналу {source.url} успешно выполнено.")

            async for message in client.iter_messages(entity, limit=self.limit):
                await self.human_delay()  # Задержка между сообщениями
                try:
                    text = message.text or ""
                    media_links: list[str] = []
                    video_links: list[str] = []
                    has_media = _message_has_real_media(message)
                    if has_media and download_media:
                        ok = await download_media_helper(message)
                        if ok:
                            local = Path("downloads") / f"{message.id}{_media_ext_for_message(message)}"
                            if local.is_file() and local.stat().st_size > 0:
                                ref = local.as_posix()
                                ext = local.suffix.lower()
                                if ext in VIDEO_EXTENSIONS:
                                    video_links.append(ref)
                                else:
                                    media_links.append(ref)

                    title = getattr(message, 'post_author', '') or getattr(entity, 'title', '')
                    checksum = ''  # Можно реализовать md5(title+source.url)
                    username = getattr(entity, "username", None)
                    post_url = (
                        f"https://t.me/{username}/{message.id}"
                        if username
                        else f"{str(source.url).rstrip('/')}/{message.id}"
                    )

                    grouped_id = getattr(message, "grouped_id", None)
                    entries.append({
                        "id": str(message.id),
                        "source_type": "telegram",
                        "source_name": getattr(entity, 'title', ''),
                        "source_url": source.url,
                        "link": post_url,
                        "created_at": message.date.strftime('%Y-%m-%d %H:%M:%S') if message.date else datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                        "ingest_status": "raw",
                        "raw_title": title,
                        "raw_content": text,
                        "raw_html": "",  # Можно добавить html-версию, если есть
                        "raw_media": json.dumps(media_links + video_links),
                        "videos": "\n".join(video_links) if video_links else "",
                        "cover_image_url": media_links[0] if media_links else "",
                        "raw_tags": "",  # Можно добавить теги, если есть
                        "checksum": checksum,
                        "parse_error": "",
                        "debug_info": f"msg_id={message.id}",
                        "_telegram_grouped_id": str(grouped_id) if grouped_id else "",
                        "_telegram_has_media": has_media,
                    })

                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения {message.id}: {e}")
                    entries.append({
                        "id": str(message.id),
                        "source_type": "telegram",
                        "source_name": getattr(entity, 'title', ''),
                        "source_url": source.url,
                        "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                        "ingest_status": "error",
                        "raw_title": "",
                        "raw_content": "",
                        "raw_html": "",
                        "raw_media": "[]",
                        "raw_tags": "",
                        "checksum": "",
                        "parse_error": str(e),
                        "debug_info": f"msg_id={getattr(message, 'id', '')}"
                    })

            for entry in _finalize_entries(entries, source.url):
                yield entry

        except Exception as e:
            logger.error(f"Ошибка парсинга {source.url}: {e}")
            for entry in _finalize_entries(entries, source.url):
                yield entry
            yield {
                "id": "",
                "source_type": "telegram",
                "source_name": getattr(source, 'name', ''),
                "source_url": getattr(source, 'url', ''),
                "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                "ingest_status": "error",
                "raw_title": "",
                "raw_content": "",
                "raw_html": "",
                "raw_media": "[]",
                "raw_tags": "",
                "checksum": "",
                "parse_error": str(e),
                "debug_info": ""
            }

    async def human_delay(self):
        """Короткая пауза между запросами (раньше 1.5–4 с раздували полный сбор)."""
        delay = random.uniform(0.25, 0.7)
        logger.debug("Задержка: %.2f сек", delay)
        await asyncio.sleep(delay)


def _message_has_real_media(message) -> bool:
    """Фото/видео/документ; превью ссылки (MessageMediaWebPage) медиа не считаем."""
    media = getattr(message, "media", None)
    if not media:
        return False
    return type(media).__name__ != "MessageMediaWebPage"


def _json_list(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)]
    if isinstance(decoded, list):
        return [str(x) for x in decoded if x]
    return []


def _msg_id_int(entry: dict) -> int:
    try:
        return int(str(entry.get("id") or "0"))
    except ValueError:
        return 0


def _merge_album(group: list[dict], grouped_id: str) -> dict:
    """Альбом Telegram = N сообщений с одним grouped_id; подпись обычно только у одного."""
    with_text = [e for e in group if str(e.get("raw_content") or "").strip()]
    base = with_text[0] if with_text else min(group, key=_msg_id_int)
    merged = dict(base)

    media: list[str] = []
    videos: list[str] = []
    for entry in group:
        for ref in _json_list(entry.get("raw_media")):
            if ref not in media:
                media.append(ref)
        for ref in str(entry.get("videos") or "").splitlines():
            ref = ref.strip()
            if ref and ref not in videos:
                videos.append(ref)
    merged["raw_media"] = json.dumps(media, ensure_ascii=False)
    merged["videos"] = "\n".join(videos)

    cover = str(base.get("cover_image_url") or "").strip()
    if not cover:
        cover = next(
            (str(e.get("cover_image_url")).strip() for e in group if str(e.get("cover_image_url") or "").strip()),
            "",
        )
    merged["cover_image_url"] = cover

    if not str(merged.get("media_json") or "").strip():
        merged["media_json"] = next((e.get("media_json") for e in group if e.get("media_json")), "")

    merged["source_item_id"] = f"album:{grouped_id}"
    merged["_telegram_has_media"] = any(e.get("_telegram_has_media") for e in group) or bool(media)
    ids = ",".join(str(e.get("id") or "") for e in group)
    merged["debug_info"] = f"{base.get('debug_info') or ''} merged_msg_ids={ids}".strip()
    return merged


def _merge_grouped_entries(entries: list[dict]) -> list[dict]:
    """Склеить сообщения одного альбома в одну запись; порядок — по первому вхождению."""
    groups: dict[str, list[dict]] = {}
    order: list[tuple[str, object]] = []
    for entry in entries:
        gid = str(entry.get("_telegram_grouped_id") or "").strip()
        if not gid:
            order.append(("single", entry))
            continue
        if gid not in groups:
            groups[gid] = []
            order.append(("group", gid))
        groups[gid].append(entry)

    out: list[dict] = []
    for kind, value in order:
        if kind == "single":
            out.append(value)  # type: ignore[arg-type]
            continue
        group = groups[value]  # type: ignore[index]
        out.append(group[0] if len(group) == 1 else _merge_album(group, value))  # type: ignore[arg-type]
    return out


def _has_review_payload(entry: dict) -> bool:
    """Запись без текста и без медиа даёт пустую карточку «Пост из … #N» — её не сохраняем."""
    if str(entry.get("parse_error") or "").strip():
        return True
    if str(entry.get("raw_content") or "").strip():
        return True
    if entry.get("_telegram_has_media"):
        return True
    if _json_list(entry.get("raw_media")) or str(entry.get("videos") or "").strip():
        return True
    if str(entry.get("cover_image_url") or "").strip():
        return True
    media_json = entry.get("media_json")
    if media_json:
        try:
            decoded = json.loads(media_json) if isinstance(media_json, str) else media_json
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict) and str(decoded.get("url") or "").strip():
            return True
    return False


def _finalize_entries(entries: list[dict], source_url: str) -> list[dict]:
    """Склеить альбомы, отбросить пустые записи; entries очищается (защита от двойной выдачи)."""
    batch = list(entries)
    entries.clear()
    merged = _merge_grouped_entries(batch)
    result: list[dict] = []
    for entry in merged:
        if not _has_review_payload(entry):
            continue
        entry.pop("_telegram_grouped_id", None)
        entry.pop("_telegram_has_media", None)
        result.append(entry)
    if len(result) != len(batch):
        logger.info(
            "telegram_parse source=%s messages=%s items=%s albums_merged=%s skipped_empty=%s",
            source_url,
            len(batch),
            len(result),
            len(batch) - len(merged),
            len(merged) - len(result),
        )
    return result