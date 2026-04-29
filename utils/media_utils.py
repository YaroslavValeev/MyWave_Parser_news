import os
import logging
import asyncio
from telethon import types
from telethon.errors import RPCError
from config.settings import config

logger = logging.getLogger(__name__)

async def download_media(message: types.Message, download_dir: str = "downloads/") -> tuple:
    """Возвращает (путь_к_файлу, тип_медиа) или (None, None) при ошибке"""
    try:
        if not message.media:
            return None, None

        # Создаем папку если нет
        os.makedirs(download_dir, exist_ok=True)

        if isinstance(message.media, types.MessageMediaPhoto):
            ext = ".jpg"
            media_type = "photo"
        elif isinstance(message.media, types.MessageMediaDocument):
            ext = _get_document_extension(message.media.document)
            media_type = "document"
        else:
            return None, None

        file_path = os.path.join(download_dir, f"{message.id}{ext}")
        await message.download_media(file=file_path)
        await asyncio.sleep(config.MEDIA_DOWNLOAD_DELAY)
        return file_path, media_type

    except RPCError as e:
        logger.error(f"Telegram RPC error: {e}")
    except Exception as e:
        logger.error(f"Media download error: {e}")
    return None, None

def _get_document_extension(document: types.Document) -> str:
    """Определяет расширение для документа"""
    for attr in document.attributes:
        if isinstance(attr, types.DocumentAttributeFilename):
            return os.path.splitext(attr.file_name)[1]
    return ".unknown"