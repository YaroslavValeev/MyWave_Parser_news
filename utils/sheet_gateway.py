"""
Единый модуль для работы с Google Sheets (gateway pattern).
Объединяет всю логику записи/обновления данных в Sheets.

Этот модуль является единой точкой входа для всех операций с Google Sheets,
заменяя разрозненные классы GoogleSheets из storage/ и services/.
"""

import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

from config.settings import config
from utils.card_preview_text import normalize_raw_feed_card_fields
from utils.contract_schema import SITE_OWNED_FIELDS
from utils.media_utils import normalize_media_contract_fields
from utils.sheet_schema import RAW_FEED_COLUMNS, DEFAULTS
from utils.row_utils import generate_checksum, validate_raw_row

logger = logging.getLogger(__name__)

# Кэш для документа и листов (чтобы не переоткрывать каждый раз)
_doc_cache: Optional[Any] = None
_worksheets_cache: Dict[str, Any] = {}
_worksheet_state_cache: Dict[str, Dict[str, Any]] = {}


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
            try:
                from services.competitions_ticker_sync import ensure_competitions_sheet_headers

                await ensure_competitions_sheet_headers(doc)
            except Exception as exc:
                logger.warning("competitions_ticker headers ensure skipped: %s", exc)
            try:
                from services.user_messages_sync import ensure_user_messages_headers

                await ensure_user_messages_headers(doc)
            except Exception as exc:
                logger.warning("user_messages headers ensure skipped: %s", exc)
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


def _worksheet_cache_key(doc, sheet_name: str) -> str:
    return f"{id(doc)}_{sheet_name}"


def _invalidate_worksheet_state(doc, sheet_name: str) -> None:
    _worksheet_state_cache.pop(_worksheet_cache_key(doc, sheet_name), None)


def _load_worksheet_state(doc, ws, sheet_name: str) -> Dict[str, Any]:
    cache_key = _worksheet_cache_key(doc, sheet_name)
    cached = _worksheet_state_cache.get(cache_key)
    if cached is not None:
        return cached

    all_values = ws.get_all_values()
    header = all_values[0] if all_values else []
    header_to_idx = {
        col_name.strip(): idx
        for idx, col_name in enumerate(header)
        if col_name and col_name.strip()
    }
    row_lookup: Dict[str, Dict[str, int]] = {}
    for field_name in ("checksum", "id"):
        idx = header_to_idx.get(field_name)
        if idx is None:
            continue
        lookup_map: Dict[str, int] = {}
        for row_num, row in enumerate(all_values[1:], start=2):
            if len(row) <= idx:
                continue
            value = str(row[idx]).strip()
            if value:
                lookup_map[value] = row_num
        row_lookup[field_name] = lookup_map

    cached = {
        "header": header,
        "header_to_idx": header_to_idx,
        "row_lookup": row_lookup,
    }
    _worksheet_state_cache[cache_key] = cached
    return cached


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
        await save_to_sheet(doc, sheet_name, items, existing_checksums=existing_checksums, ws_cache=ws)
        return len(items)
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
    ws = get_worksheet(doc, sheet_name)
    if ws is None:
        return False

    if sheet_name == "raw_feed":
        item_data = normalize_media_contract_fields(item_data)
        normalize_raw_feed_card_fields(item_data)

    lookup_value = str(item_data.get(lookup_field) or "").strip()
    if not lookup_value:
        logger.warning(f"Значение {lookup_field} отсутствует в item_data")
        return False

    try:
        state = _load_worksheet_state(doc, ws, sheet_name)
        header = state["header"]
        if not header:
            logger.debug(f"Лист {sheet_name} пуст или содержит только заголовки - обновление невозможно")
            return False

        header_to_idx = state["header_to_idx"]
        lookup_idx = header_to_idx.get(lookup_field)
        if lookup_idx is None:
            logger.error(f"Поле {lookup_field} не найдено в заголовках листа {sheet_name}")
            return False

        row_num = state["row_lookup"].get(lookup_field, {}).get(lookup_value)
        if not row_num:
            _invalidate_worksheet_state(doc, sheet_name)
            state = _load_worksheet_state(doc, ws, sheet_name)
            header_to_idx = state["header_to_idx"]
            row_num = state["row_lookup"].get(lookup_field, {}).get(lookup_value)
            if not row_num:
                logger.debug(f"Строка с {lookup_field}={lookup_value} не найдена в листе {sheet_name}")
                return False

        updates = []
        updated_fields: list[str] = []
        for col_name, col_value in item_data.items():
            if col_name == lookup_field:
                continue
            if sheet_name == "raw_feed" and col_name in SITE_OWNED_FIELDS:
                logger.debug(f"Пропуск SITE-owned поля при update: {col_name}")
                continue
            col_idx = header_to_idx.get(str(col_name).strip())
            if col_idx is None:
                continue
            updates.append(
                {
                    "range": rowcol_to_a1(row_num, col_idx + 1),
                    "values": [[col_value if col_value is not None else ""]],
                }
            )
            updated_fields.append(col_name)

        if not updates:
            logger.warning(f"Нет полей для обновления в строке {row_num}")
            return False

        ws.batch_update(updates, value_input_option="RAW")
        logger.info(
            f"Обновлена строка {row_num} в листе {sheet_name} по {lookup_field}={lookup_value}. "
            f"Обновлены поля: {', '.join(updated_fields)}"
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления строки в листе {sheet_name}: {e}", exc_info=True)
        return False


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


def _normalize_sheet_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def append_raw_feed_rows(doc, items: List[Dict[str, Any]]) -> int:
    """
    Прямая запись строк в raw_feed по каноничной схеме RAW_FEED_COLUMNS.
    Используется для ingest/backfill, когда важно минимизировать число read-запросов
    и не проходить через общий legacy save_to_sheet.
    """
    ws = get_worksheet(doc, "raw_feed")
    if ws is None:
        return 0

    header = ws.row_values(1)
    if not header:
        ws.append_row(RAW_FEED_COLUMNS, value_input_option="RAW")
        header = list(RAW_FEED_COLUMNS)
    else:
        missing_columns = [col for col in RAW_FEED_COLUMNS if col not in header]
        if missing_columns:
            header = header + missing_columns
            ws.resize(cols=len(header))
            last = rowcol_to_a1(1, len(header))
            ws.update(
                range_name=f"A1:{last}",
                values=[header],
                value_input_option="RAW",
            )
            logger.info(
                "raw_feed: appended missing columns without reordering existing header: %s",
                ", ".join(missing_columns),
            )

    if not header:
        return 0

    if len(header) > ws.col_count:
        ws.resize(cols=len(header))

    # Критично: не перезаписываем существующий header под RAW_FEED_COLUMNS.
    # В raw_feed уже есть данные, поэтому смена порядка заголовков смещает
    # смысл старых строк. Новые строки пишем строго в текущем порядке листа.
    if len(header) != len(ws.row_values(1)):
        last = rowcol_to_a1(1, len(header))
        ws.update(
            range_name=f"A1:{last}",
            values=[header],
            value_input_option="RAW",
        )

    all_values = ws.get_all_values()
    data_rows = all_values[1:] if len(all_values) > 1 else []
    next_row_number = len(data_rows) + 2

    try:
        i_st = header.index("source_type")
        i_sn = header.index("source_name")
        i_sid = header.index("source_item_id")
    except ValueError:
        i_st = i_sn = i_sid = -1

    try:
        i_checksum = header.index("checksum")
    except ValueError:
        i_checksum = -1

    existing_source_item_ids = set()
    existing_checksums = set()
    for row in data_rows:
        if i_sid >= 0 and len(row) > max(i_st, i_sn, i_sid) and row[i_sid]:
            existing_source_item_ids.add(
                (str(row[i_st]).strip(), str(row[i_sn]).strip(), str(row[i_sid]).strip())
            )
        if i_checksum >= 0 and len(row) > i_checksum and row[i_checksum]:
            existing_checksums.add(str(row[i_checksum]).strip())

    prepared_rows: list[list[str]] = []
    for item in items:
        normalized = normalize_media_contract_fields(item)
        checksum = str(normalized.get("checksum") or "").strip()
        if not checksum:
            checksum = generate_checksum(normalized)
            normalized["checksum"] = checksum

        source_key = (
            str(normalized.get("source_type") or "").strip(),
            str(normalized.get("source_name") or "").strip(),
            str(normalized.get("source_item_id") or "").strip(),
        )
        if source_key[2] and source_key in existing_source_item_ids:
            continue
        if checksum in existing_checksums:
            continue

        for col_name in header:
            if col_name not in normalized or normalized[col_name] is None:
                normalized[col_name] = DEFAULTS.get(col_name, "")

        if not normalized.get("created_at"):
            normalized["created_at"] = datetime.now(timezone.utc).isoformat()
        if not normalized.get("updated_at"):
            normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not normalized.get("row_number"):
            normalized["row_number"] = str(next_row_number)

        if not validate_raw_row(normalized):
            continue

        prepared_rows.append([_normalize_sheet_value(normalized.get(col, "")) for col in header])
        next_row_number += 1
        if source_key[2]:
            existing_source_item_ids.add(source_key)
        existing_checksums.add(checksum)

    if not prepared_rows:
        return 0

    ws.append_rows(prepared_rows, value_input_option="RAW")
    _invalidate_worksheet_state(doc, "raw_feed")
    return len(prepared_rows)


def clear_cache():
    """Очищает кэш документов и листов"""
    global _doc_cache, _worksheets_cache, _worksheet_state_cache
    _doc_cache = None
    _worksheets_cache.clear()
    _worksheet_state_cache.clear()
    logger.debug("Кэш sheet gateway очищен")
