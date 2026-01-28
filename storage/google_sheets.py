import gspread
from google.oauth2.service_account import Credentials
from config.settings import config
import logging
from utils.sheet_schema import RAW_FEED_COLUMNS, DEFAULTS
from utils.row_utils import validate_raw_row

logger = logging.getLogger(__name__)

class GoogleSheets:
    def __init__(self):
        try:
            credentials_file = config.GOOGLE_CREDENTIALS_FILE  # добавлено: извлечение из config
            creds = Credentials.from_service_account_file(
                credentials_file,  # изменено: использование credentials_file
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            self.client = gspread.authorize(creds)
            spreadsheet = self.client.open_by_key(config.GOOGLE_SHEET_ID)
            # Пытаемся открыть лист "raw_feed", если не существует - используем первый лист
            try:
                self.sheet = spreadsheet.worksheet("raw_feed")
                logger.info(f"Google Sheets подключен: лист 'raw_feed'")
            except Exception:
                try:
                    self.sheet = spreadsheet.sheet1
                    logger.warning(f"Лист 'raw_feed' не найден, используется первый лист")
                except Exception:
                    logger.error("Не удалось открыть лист в Google Sheets")
                    self.sheet = None
        except Exception as e:
            logger.error(f"Failed to connect to Google Sheets: {e}")
            self.sheet = None

    def append_news(self, news):
        """
        DEPRECATED (P0): позиционная запись запрещена.

        Для raw_feed используйте `append_news_batch()` с dict/объектом (header-based),
        либо async-API `utils.import_asyncio.save_to_sheet(...)`.
        """
        if not self.sheet:
            logger.error("Google Sheets не настроен. Запись невозможна.")
            return
        if isinstance(news, list):
            raise RuntimeError("P0: append_news(list) запрещён (позиционная запись). Используйте header-based API.")
        self.append_news_batch([news])

    def append_news_batch(self, news_items):
        """
        Пакетная запись новостей в Google Sheets (raw_feed).
        Использует header-based запись: значения записываются по названиям колонок, а не по позициям.
        """
        if not self.sheet:
            logger.error("Google Sheets не настроен. Пакетная запись невозможна.")
            return
        try:
            # Читаем заголовки и текущие данные (нужно для детерминированного row_number)
            all_values = self.sheet.get_all_values()
            header = all_values[0] if all_values else []
            if not header:
                # Если заголовков нет, создаем их из схемы (каноничная схема = 68 колонок)
                self.sheet.append_row(RAW_FEED_COLUMNS)
                header = RAW_FEED_COLUMNS
                all_values = [header]
                logger.info(f"Созданы заголовки для листа raw_feed: {len(RAW_FEED_COLUMNS)} колонок")

            header_to_idx = {h.strip(): i for i, h in enumerate(header) if h and h.strip()}
            if "row_number" not in header_to_idx:
                raise RuntimeError(
                    "P0: В листе raw_feed отсутствует колонка 'row_number' в заголовках. "
                    "Остановка записи, чтобы сайт не сделал небезопасный writeback."
                )

            next_row_number = len(all_values) + 1  # next data row (1-based)
            
            # Функция для получения значения поля из объекта или словаря
            def get_value(item, col_name):
                """Получает значение поля из объекта (getattr) или словаря (dict.get)"""
                if isinstance(item, dict):
                    return item.get(col_name, DEFAULTS.get(col_name, ''))
                else:
                    return getattr(item, col_name, DEFAULTS.get(col_name, ''))
            
            # Функция для преобразования объекта в словарь для валидации
            def item_to_dict(item):
                """Преобразует объект в словарь для валидации"""
                if isinstance(item, dict):
                    return item
                else:
                    return {col: get_value(item, col) for col in RAW_FEED_COLUMNS}
            
            # Формируем строки по заголовкам с валидацией
            rows = []
            for item in news_items:
                # Преобразуем в словарь для валидации
                item_dict = item_to_dict(item)
                
                # Гарантируем заполнение обязательных полей согласно контракту
                from utils.row_utils import ensure_raw_title, normalize_ingest_status
                from datetime import datetime, timezone
                
                # Гарантируем raw_title
                item_dict = ensure_raw_title(item_dict)
                if isinstance(item, dict):
                    item.update(item_dict)
                else:
                    for k, v in item_dict.items():
                        setattr(item, k, v)
                
                # Гарантируем created_at если пусто
                if not item_dict.get('created_at'):
                    item_dict['created_at'] = datetime.now(timezone.utc).isoformat()
                    if isinstance(item, dict):
                        item['created_at'] = item_dict['created_at']
                    else:
                        setattr(item, 'created_at', item_dict['created_at'])
                
                # Гарантируем ingest_status и связанные поля
                ingest_status = normalize_ingest_status(item_dict.get('ingest_status', 'ok'))
                if isinstance(item, dict):
                    item['ingest_status'] = ingest_status
                    if not item.get('ingest_attempts'):
                        item['ingest_attempts'] = 1
                    if not item.get('ingest_last_try_at'):
                        item['ingest_last_try_at'] = datetime.now(timezone.utc).isoformat()
                else:
                    setattr(item, 'ingest_status', ingest_status)
                    if not hasattr(item, 'ingest_attempts') or not getattr(item, 'ingest_attempts'):
                        setattr(item, 'ingest_attempts', 1)
                    if not hasattr(item, 'ingest_last_try_at') or not getattr(item, 'ingest_last_try_at'):
                        setattr(item, 'ingest_last_try_at', datetime.now(timezone.utc).isoformat())
                
                # Валидируем строку перед записью
                from utils.row_utils import validate_raw_row, validate_status_consistency
                is_valid, error_msg = validate_raw_row(item_dict, strict=False)
                if not is_valid:
                    # Если валидация не прошла, устанавливаем ошибку, но всё равно записываем
                    if isinstance(item, dict):
                        item['ingest_status'] = 'error'
                        item['ingest_error'] = error_msg
                        item['parse_error'] = error_msg
                        # КРИТИЧНО: если ingest_status=error, не позволяем status=PUBLISHED
                        if item.get('status', '').upper() == 'PUBLISHED':
                            item['status'] = 'ERROR'
                    else:
                        setattr(item, 'ingest_status', 'error')
                        setattr(item, 'ingest_error', error_msg)
                        setattr(item, 'parse_error', error_msg)
                        if getattr(item, 'status', '').upper() == 'PUBLISHED':
                            setattr(item, 'status', 'ERROR')
                    logger.warning(f"Запись не прошла валидацию, будет записана с ingest_status=error: {item_dict.get('id', 'unknown')} - {error_msg}")
                
                # Проверяем согласованность статусов (запрещено ingest_status=skipped/error с status=PUBLISHED)
                status_consistency_valid, status_error_msg = validate_status_consistency(item_dict)
                if not status_consistency_valid:
                    if isinstance(item, dict):
                        # Если ingest_status=skipped/error, но status=PUBLISHED — исправляем status
                        if item.get('status', '').upper() == 'PUBLISHED':
                            item['status'] = 'DISCARDED' if ingest_status == 'skipped' else 'ERROR'
                            logger.warning(f"Исправлено недопустимое сочетание статусов для id={item_dict.get('id', 'unknown')}: status изменен с PUBLISHED на {item['status']}")
                    else:
                        if getattr(item, 'status', '').upper() == 'PUBLISHED':
                            new_status = 'DISCARDED' if ingest_status == 'skipped' else 'ERROR'
                            setattr(item, 'status', new_status)
                            logger.warning(f"Исправлено недопустимое сочетание статусов для id={item_dict.get('id', 'unknown')}: status изменен с PUBLISHED на {new_status}")

                # P0: Проставляем row_number ДО вставки (детерминированно)
                item_row_number = next_row_number + len(rows)
                if isinstance(item, dict):
                    item["row_number"] = str(item_row_number)
                else:
                    setattr(item, "row_number", str(item_row_number))

                row = [get_value(item, col) for col in header]

                rn_idx = header_to_idx.get("row_number")
                if rn_idx is None or not str(row[rn_idx]).strip():
                    raise RuntimeError(
                        "P0: Не удалось определить/записать row_number для новой строки raw_feed. "
                        "Остановка записи, чтобы сайт не сделал небезопасный writeback."
                    )
                rows.append(row)
            
            if rows:
                self.sheet.append_rows(rows, value_input_option="RAW")
                logger.info(f"Добавлено {len(rows)} записей (batch) в Google Sheets (header-based).")
        except Exception as e:
            logger.error(f"Ошибка пакетной записи в Google Sheets: {e}", exc_info=True)

# Пример использования:
# gs = GoogleSheets()
# gs.append_news(["1", "Заголовок", "Текст новости", "https://source.com"])
