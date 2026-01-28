"""
Collect sources and ingest into the new DB schema (items table).

This module intentionally focuses on *ingestion into SQLite* (items/checksum/status=new)
and does not depend on Google Sheets.
"""

import asyncio
import hashlib
import json
import logging
import inspect
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional

from config.settings import config
from storage.repository import repository
from storage.sources import list_sources, NewsSource

logger = logging.getLogger(__name__)


async def _save_to_google_sheets_async(item_id: int, item_data: Dict[str, Any]):
    """
    Асинхронная функция для сохранения в Google Sheets
    """
    try:
        # P0: только header-based запись по заголовкам листа + DEFAULTS
        # и обязательное заполнение row_number при вставке новой строки
        from utils.import_asyncio import init_google_sheets, save_to_sheet

        doc = await init_google_sheets()
        if not doc:
            return

        row_dict = {
            "id": str(item_id),
            "source_type": item_data.get("source_type", ""),
            "source_name": item_data.get("source_name", ""),
            "source_url": item_data.get("source_url", ""),
            "created_at": item_data.get("created_at", ""),
            "ingest_status": "ok",
            "raw_title": item_data.get("raw_title", ""),
            "raw_content": item_data.get("raw_content", ""),
            "raw_html": item_data.get("raw_html", ""),
            "raw_media": item_data.get("raw_media", ""),
            "lang": item_data.get("lang", ""),
            "raw_tags": item_data.get("raw_tags", ""),
            "checksum": item_data.get("checksum", ""),
            "status": "DRAFT",
        }

        # Вставка (append) с автопроставлением row_number внутри save_to_sheet (fail-fast если не получилось)
        await save_to_sheet(doc, "raw_feed", [row_dict])
        logger.debug(f"Item {item_id} сохранен в Google Sheets (header-based)")
    except Exception as e:
        logger.warning(f"Не удалось сохранить item {item_id} в Google Sheets: {e}")


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _compute_checksum(raw_title: str, url: str) -> str:
    """
    Устаревшая функция - используйте generate_checksum из utils.row_utils вместо этого.
    Оставлена для обратной совместимости, но теперь генерирует checksum на основе содержимого.
    """
    # Используем новую логику: raw_title + raw_content + raw_html (без url)
    from utils.row_utils import generate_checksum
    return generate_checksum({'raw_title': raw_title or '', 'raw_content': '', 'raw_html': ''})


def _safe_json(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _normalize_to_item(row: Any, source_type: str, source_name: str, source_url: str) -> Optional[Dict[str, Any]]:
    """
    Normalize various collector outputs to repository.create_item payload.
    Expected final keys:
      source_type, source_name, source_url, raw_title, raw_content, raw_html, raw_media, raw_tags, lang, checksum, status
    """
    # Pydantic model / dataclass / plain dict support
    if hasattr(row, "model_dump"):
        data = row.model_dump()
    elif is_dataclass(row):
        data = asdict(row)
    elif isinstance(row, dict):
        data = row
    else:
        return None

    raw_title = (data.get("raw_title") or data.get("title") or "").strip()
    raw_content = (data.get("raw_content") or data.get("content") or "").strip()
    raw_html = data.get("raw_html") or ""
    raw_tags = data.get("raw_tags") or ""
    raw_media = data.get("raw_media") or ""

    # Prefer per-item link if collector provides it
    link = (
        data.get("link")
        or data.get("news_url")
        or data.get("source_url")
        or ""
    )
    link = str(link).strip()

    # YouTube collector stores item link in debug_info=yt_link=...
    if (not link) and isinstance(data.get("debug_info"), str) and "yt_link=" in data["debug_info"]:
        try:
            link = data["debug_info"].split("yt_link=", 1)[1].strip()
        except Exception:
            link = ""

    # Fallback to passed-in source_url (channel/feed url) only if we have no per-item url
    final_url = link or source_url

    # Normalize media as JSON string if it isn't already
    raw_media = _safe_json(raw_media)

    # Генерируем checksum если его нет (на основе содержимого: raw_title + raw_content + raw_html)
    from utils.row_utils import generate_checksum
    if not data.get("checksum"):
        checksum = generate_checksum({
            'raw_title': raw_title,
            'raw_content': raw_content,
            'raw_html': raw_html
        })
    else:
        checksum = data.get("checksum")

    return {
        "source_type": source_type,
        "source_name": source_name,
        "source_url": final_url,
        "raw_title": raw_title,
        "raw_content": raw_content,
        "raw_html": raw_html,
        "raw_media": raw_media,
        "raw_tags": raw_tags,
        "lang": data.get("lang") or getattr(config, "NL_LANG", "ru"),
        "checksum": checksum,
        "status": "new",
    }


async def collect_sources(limit_per_source: int = 30, filter_keywords: Optional[List[str]] = None, date_from=None, date_to=None) -> Dict[str, int]:
    """
    Collect from configured sources and insert new items into SQLite.
    Returns counters: collected/inserted/duplicates/errors.
    
    Источники читаются из БД (таблица sources), если БД пуста - используется storage/sources.py как fallback
    """
    counters = {"sources": 0, "collected": 0, "inserted": 0, "duplicates": 0, "errors": 0}
    
    # Сначала пытаемся получить источники из БД
    db_sources = await repository.list_sources(enabled_only=True)
    
    # Преобразуем источники из БД в формат NewsSource для совместимости с парсерами
    sources = []
    if db_sources:
        for db_source in db_sources:
            # Создаем объект NewsSource из данных БД
            news_source = NewsSource(
                type=db_source.get('type', ''),
                url=db_source.get('url', ''),
                name=db_source.get('name', ''),
                filter=db_source.get('use_filter', True),
                last_id=db_source.get('last_id')
            )
            sources.append(news_source)
        logger.info(f"Используется {len(sources)} источников из БД")
    else:
        # Если БД пуста, используем источники из storage/sources.py (fallback)
        sources = list_sources()
        logger.info(f"БД источников пуста, используется {len(sources)} источников из storage/sources.py")
        # Если есть источники в storage/sources.py, мигрируем их в БД
        if sources:
            try:
                from utils.migrate_sources_to_db import migrate_sources_to_db
                await migrate_sources_to_db()
                # После миграции снова читаем из БД
                db_sources = await repository.list_sources(enabled_only=True)
                sources = []
                for db_source in db_sources:
                    news_source = NewsSource(
                        type=db_source.get('type', ''),
                        url=db_source.get('url', ''),
                        name=db_source.get('name', ''),
                        filter=db_source.get('use_filter', True),
                        last_id=db_source.get('last_id')
                    )
                    sources.append(news_source)
                logger.info(f"После миграции используется {len(sources)} источников из БД")
            except Exception as e:
                logger.warning(f"Не удалось мигрировать источники в БД: {e}, продолжаю с storage/sources.py")
    
    counters["sources"] = len(sources)

    tg_client = None
    session_manager = None
    # Telethon is optional; collectors should still work without it
    if config.TELEGRAM_API_ID_USER and config.TELEGRAM_API_HASH_USER and config.TELEGRAM_PHONE:
        try:
            import sys
            import os
            # Импортируем из корня проекта
            root_dir = os.path.dirname(os.path.dirname(__file__))
            sys.path.insert(0, root_dir)
            from telegram_session import TelegramSessionManager

            session_manager = TelegramSessionManager(
                config.TELEGRAM_API_ID_USER,
                config.TELEGRAM_API_HASH_USER,
                config.TELEGRAM_PHONE,
            )
            tg_client = await session_manager.get_client()
            is_authorized = tg_client.is_user_authorized() if tg_client else False
            if inspect.isawaitable(is_authorized):
                is_authorized = await is_authorized
            if not tg_client or not is_authorized:
                tg_client = None
        except Exception as e:
            logger.warning(f"Telethon не инициализирован (пропускаю telegram источники): {e}")
            tg_client = None

    try:
        # Используем парсеры из utils/import asyncio.py
        import sys
        import os
        import importlib.util
        
        # Импортируем модуль с парсерами
        utils_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'utils', 'import asyncio.py')
        spec = importlib.util.spec_from_file_location("import_asyncio", utils_path)
        import_asyncio = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(import_asyncio)
        
        # Создаем парсеры
        parsers = {}
        if tg_client:
            parsers["telegram"] = import_asyncio.TelegramParser(tg_client, limit=limit_per_source)
        parsers["rss"] = import_asyncio.RSSParser(limit=limit_per_source)
        parsers["website"] = import_asyncio.WebsiteParser(limit=limit_per_source)
        if config.YOUTUBE_API_KEY:
            parsers["youtube"] = import_asyncio.YoutubeParser(config.YOUTUBE_API_KEY, limit=limit_per_source)
        
        for src in sources:
            try:
                src_type = getattr(src, "type", "") or ""
                src_name = getattr(src, "name", "") or getattr(src, "url", "") or ""
                src_url = getattr(src, "url", "") or ""

                parser = parsers.get(src_type)
                if not parser:
                    logger.warning(f"Парсер для типа {src_type} не найден, пропускаю источник {src_name}")
                    continue

                # Используем асинхронный парсер с фильтрацией по дате
                rows = await parser.parse(src, date_from=date_from, date_to=date_to)

                # Парсеры из utils/import asyncio.py возвращают словари с полями для Google Sheets
                # Нужно преобразовать их в формат для БД через _normalize_to_item
                for row in rows:
                    # Преобразуем данные через normalize
                    item = _normalize_to_item(row, src_type, src_name, src_url)
                    if not item:
                        continue
                    
                    counters["collected"] += 1
                    
                    # Проверяем checksum (генерируется из raw_title + raw_content + raw_html)
                    checksum = item.get("checksum", "")
                    if not checksum:
                        # Если checksum не был вычислен, вычисляем его
                        from utils.row_utils import generate_checksum
                        checksum = generate_checksum(item)
                        item["checksum"] = checksum
                    
                    # Проверяем дубликаты по checksum (который теперь основан на содержимом)
                    if await repository.item_exists_by_checksum(checksum):
                        counters["duplicates"] += 1
                        logger.debug(f"Дубликат найден по checksum (содержимое: raw_title + raw_content + raw_html)")
                        continue
                    
                    # Дополнительная проверка по содержимому (fallback для надежности)
                    raw_title = item.get("raw_title", "").strip()
                    raw_content = item.get("raw_content", "").strip()
                    raw_html = item.get("raw_html", "").strip()
                    if raw_title or raw_content or raw_html:
                        if await repository.item_exists_by_content(raw_title, raw_content, raw_html):
                            counters["duplicates"] += 1
                            logger.debug(f"Дубликат найден по содержимому (raw_title + raw_content + raw_html)")
                            continue
                    
                    # Убеждаемся, что status = 'new' для новых записей в БД
                    item['status'] = 'new'
                    
                    # Создаем запись в БД
                    new_id = await repository.create_item(item)
                    if new_id:
                        counters["inserted"] += 1
                        logger.debug(f"Добавлена запись {new_id} из источника {src_name}")
                        # Примечание: синхронизация с Google Sheets будет через отдельный процесс
                        # или через функцию sync_to_google_sheets
                    else:
                        counters["errors"] += 1
                        logger.warning(f"Не удалось создать запись в БД из {src_name}")
            except Exception as e:
                counters["errors"] += 1
                logger.error(f"Ошибка collect_sources для источника {getattr(src, 'url', '')}: {e}", exc_info=True)
    finally:
        try:
            if session_manager:
                await session_manager.close()
        except Exception:
            pass

    return counters


async def collect_single_source(source, limit_per_source: int = 30, date_from=None, date_to=None) -> Dict[str, int]:
    """
    Парсит один конкретный источник.
    
    Args:
        source: Объект NewsSource
        limit_per_source: Лимит новостей с источника
        date_from: Начальная дата для фильтрации (datetime, опционально)
        date_to: Конечная дата для фильтрации (datetime, опционально)
    
    Returns:
        Словарь со статистикой: collected, inserted, duplicates, errors
    """
    counters = {"collected": 0, "inserted": 0, "duplicates": 0, "errors": 0}
    
    tg_client = None
    session_manager = None
    
    # Инициализируем Telegram клиент если нужен
    if source.type == 'telegram':
        if config.TELEGRAM_API_ID_USER and config.TELEGRAM_API_HASH_USER and config.TELEGRAM_PHONE:
            try:
                import sys
                import os
                root_dir = os.path.dirname(os.path.dirname(__file__))
                sys.path.insert(0, root_dir)
                from telegram_session import TelegramSessionManager
                
                session_manager = TelegramSessionManager(
                    config.TELEGRAM_API_ID_USER,
                    config.TELEGRAM_API_HASH_USER,
                    config.TELEGRAM_PHONE,
                )
                tg_client = await session_manager.get_client()
                if not tg_client or not await tg_client.is_user_authorized():
                    tg_client = None
            except Exception as e:
                logger.warning(f"Telethon не инициализирован: {e}")
                tg_client = None
    
    try:
        # Импортируем парсеры
        import sys
        import os
        import importlib.util
        
        utils_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'utils', 'import asyncio.py')
        spec = importlib.util.spec_from_file_location("import_asyncio", utils_path)
        import_asyncio = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(import_asyncio)
        
        # Создаем парсер для типа источника
        parser = None
        if source.type == 'telegram' and tg_client:
            parser = import_asyncio.TelegramParser(tg_client, limit=limit_per_source)
        elif source.type == 'rss':
            parser = import_asyncio.RSSParser(limit=limit_per_source)
        elif source.type == 'website':
            parser = import_asyncio.WebsiteParser(limit=limit_per_source)
        elif source.type == 'youtube' and config.YOUTUBE_API_KEY:
            parser = import_asyncio.YoutubeParser(config.YOUTUBE_API_KEY, limit=limit_per_source)
        
        if not parser:
            logger.warning(f"Парсер для типа {source.type} не найден")
            counters["errors"] = 1
            return counters
        
        # Парсим источник с фильтрацией по дате
        rows = await parser.parse(source, date_from=date_from, date_to=date_to)
        
        # Обрабатываем результаты
        src_type = source.type
        src_name = source.name
        src_url = source.url
        
        for row in rows:
            item = _normalize_to_item(row, src_type, src_name, src_url)
            if not item:
                continue
            
            counters["collected"] += 1
            
            checksum = item.get("checksum", "")
            if not checksum:
                counters["errors"] += 1
                continue
            
            if await repository.item_exists_by_checksum(checksum):
                counters["duplicates"] += 1
                continue
            
            item['status'] = 'new'
            new_id = await repository.create_item(item)
            if new_id:
                counters["inserted"] += 1
            else:
                counters["errors"] += 1
                
    except Exception as e:
        counters["errors"] += 1
        logger.error(f"Ошибка парсинга источника {source.url}: {e}", exc_info=True)
    finally:
        if session_manager:
            try:
                await session_manager.close()
            except Exception:
                pass
    
    return counters


async def sync_new_items_to_sheets():
    """
    Синхронизирует новые записи из БД в Google Sheets
    """
    try:
        import sys
        import os
        import importlib.util
        from datetime import datetime, timezone
        
        # Импортируем модуль с функциями Google Sheets
        utils_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'utils', 'import asyncio.py')
        spec = importlib.util.spec_from_file_location("import_asyncio", utils_path)
        import_asyncio = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(import_asyncio)
        
        # Получаем все записи со статусом 'new', которые еще не синхронизированы
        items = await repository._fetchall(
            "SELECT * FROM items WHERE status = 'new' ORDER BY created_at DESC LIMIT 100"
        )
        
        if not items:
            logger.debug("Нет новых записей для синхронизации с Google Sheets")
            return 0
        
        logger.info(f"Синхронизация {len(items)} записей с Google Sheets...")
        
        doc = await import_asyncio.init_google_sheets()
        if not doc:
            logger.error("Не удалось подключиться к Google Sheets для синхронизации")
            return 0
        
        # Преобразуем записи в формат для Google Sheets
        data_list = []
        for row in items:
            item_dict = dict(row)
            
            # Преобразуем данные из БД в формат для Google Sheets
            sheet_item = {
                'id': str(item_dict.get('id', '')),
                'source_type': item_dict.get('source_type', ''),
                'source_name': item_dict.get('source_name', ''),
                'source_url': item_dict.get('source_url', ''),
                'source_item_id': '',  # Будет заполнено парсером при следующем запуске
                'created_at': item_dict.get('created_at', ''),
                'original_published_at': '',  # Будет заполнено парсером
                'raw_title': item_dict.get('raw_title', ''),
                'raw_content': item_dict.get('raw_content', ''),
                'raw_html': item_dict.get('raw_html', ''),
                'raw_media': item_dict.get('raw_media', ''),
                'media_json': '',  # Будет заполнено парсером
                'content_format': 'text',  # По умолчанию
                'lang': item_dict.get('lang', 'ru'),
                'raw_tags': item_dict.get('raw_tags', ''),
                'status': 'DRAFT',  # На этапе ingest
                'ingest_status': 'OK',
                'parse_error': '',
                'updated_at': item_dict.get('updated_at', datetime.now(timezone.utc).isoformat()),
                'checksum': item_dict.get('checksum', ''),
                'debug_info': '',
                # Остальные поля будут пустыми до AI обработки
                'summary': '',
                'questions': '',
                'ne': '',
                'need_opinion': '',
                'expert_opinion': '',
                'user_answers': '',
                'final_posts': '',
                'final_version': '',
                'final_ready': '',
                'published_posts': '',
                'published_at': '',
                'publish_attempts': '',
                'publish_last_try_at': '',
                'publish_error': '',
                'review_queue': ''
            }
            
            data_list.append(sheet_item)
        
        await import_asyncio.save_to_sheet(doc, 'raw_feed', data_list)
        logger.info(f"✅ Синхронизировано {len(data_list)} записей с Google Sheets")
        return len(data_list)
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации с Google Sheets: {e}", exc_info=True)
        return 0


