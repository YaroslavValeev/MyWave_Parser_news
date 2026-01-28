"""
Единый модуль для работы с Google Sheets (gateway pattern).
Объединяет всю логику записи/обновления данных в Sheets.

Этот модуль является единой точкой входа для всех операций с Google Sheets,
заменяя разрозненные классы GoogleSheets из storage/ и services/.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from config.settings import config
from utils.sheet_schema import RAW_FEED_COLUMNS, DEFAULTS
from utils.row_utils import generate_checksum, validate_raw_row

logger = logging.getLogger(__name__)

# Кэш для документа и листов (чтобы не переоткрывать каждый раз)
_doc_cache: Optional[Any] = None
_worksheets_cache: Dict[str, Any] = {}


async def init_sheet_gateway():
    """
    Инициализация gateway для работы с Google Sheets.
    Возвращает документ gspread для использования в других функциях.
    """
    global _doc_cache
    
    if _doc_cache is not None:
        return _doc_cache
    
    try:
        from utils.import_asyncio import init_google_sheets, ensure_sheet_headers
        
        doc = await init_google_sheets()
        if doc:
            # Автоприведение заголовков для raw_feed
            await ensure_sheet_headers(doc, 'raw_feed')
            _doc_cache = doc
            logger.info("Sheet gateway инициализирован")
        return doc
    except Exception as e:
        logger.error(f"Ошибка инициализации sheet gateway: {e}", exc_info=True)
        return None


def get_worksheet(doc, sheet_name: str):
    """Получает worksheet из документа, используя кэш"""
    global _worksheets_cache
    
    cache_key = f"{id(doc)}_{sheet_name}"
    if cache_key in _worksheets_cache:
        return _worksheets_cache[cache_key]
    
    try:
        ws = doc.worksheet(sheet_name)
        _worksheets_cache[cache_key] = ws
        return ws
    except Exception as e:
        logger.error(f"Не найден лист {sheet_name}: {e}")
        return None


async def append_items_batch(doc, sheet_name: str, items: List[Dict[str, Any]], 
                             existing_checksums: Optional[set] = None) -> int:
    """
    Пакетная запись элементов в лист Google Sheets с header-based подходом.
    
    :param doc: gspread документ
    :param sheet_name: имя листа (например, 'raw_feed')
    :param items: список словарей с данными
    :param existing_checksums: множество существующих checksum для дедупликации
    :return: количество записанных строк
    """
    # P0: ЕДИНЫЙ путь записи — каноничная header-based функция из utils.import_asyncio
    # (там же enforced row_number для raw_feed и DEFAULTS по заголовкам).
    from utils.import_asyncio import save_to_sheet

    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return 0

    try:
        before = len(ws.get_all_values())
        await save_to_sheet(doc, sheet_name, items, existing_checksums=existing_checksums, ws_cache=ws)
        after = len(ws.get_all_values())
        # Разница по строкам (учитываем, что заголовок уже есть)
        return max(0, after - before)
    except Exception as e:
        logger.error(f"Ошибка пакетной записи в лист {sheet_name}: {e}", exc_info=True)
        raise


async def update_item(doc, sheet_name: str, item_data: Dict[str, Any], 
                      lookup_field: str = 'checksum') -> bool:
    """
    Обновляет существующую строку в Google Sheets по lookup_field.
    
    :param doc: gspread документ
    :param sheet_name: имя листа
    :param item_data: словарь с данными для обновления
    :param lookup_field: поле для поиска строки ('checksum' или 'id')
    :return: True если успешно обновлено, False в противном случае
    """
    from utils.import_asyncio import SHEET_COLUMNS, update_sheet_row
    
    # Используем существующую функцию update_sheet_row из import asyncio
    return await update_sheet_row(doc, sheet_name, item_data, lookup_field)


async def get_existing_checksums(doc, sheet_name: str = 'raw_feed') -> set:
    """
    Получает множество существующих checksum из листа.
    
    :param doc: gspread документ
    :param sheet_name: имя листа
    :return: множество checksum
    """
    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return set()
    
    try:
        all_values = ws.get_all_values()
        if not all_values or len(all_values) < 2:
            return set()
        
        header = all_values[0]
        try:
            checksum_idx = header.index("checksum")
        except ValueError:
            return set()
        
        checksums = set()
        for row in all_values[1:]:
            if len(row) > checksum_idx and row[checksum_idx]:
                checksums.add(row[checksum_idx])
        
        return checksums
    except Exception as e:
        logger.error(f"Ошибка получения checksum из листа {sheet_name}: {e}")
        return set()


def clear_cache():
    """Очищает кэш документов и листов"""
    global _doc_cache, _worksheets_cache
    _doc_cache = None
    _worksheets_cache.clear()
    logger.debug("Кэш sheet gateway очищен")
