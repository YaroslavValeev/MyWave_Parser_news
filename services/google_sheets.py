import gspread
from oauth2client.service_account import ServiceAccountCredentials
from core.models import NewsItem
from config.settings import config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GoogleSheets:
    def __init__(self, creds_file: str | None = None, sheet_id: str | None = None):
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # Prefer values passed explicitly, fall back to config
        creds_file = creds_file or getattr(config, 'GOOGLE_CREDENTIALS_FILE', None)
        sheet_id = sheet_id or getattr(config, 'GOOGLE_SHEET_ID', None)
        self._disabled = False
        try:
            if not creds_file or not sheet_id:
                raise RuntimeError('Google credentials or sheet id not configured')
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(sheet_id).worksheet("raw_feed")
            logger.info(f"Google Sheets подключен: {sheet_id} [raw_feed]")
        except Exception as e:
            # If anything goes wrong (invalid key, JWT issue, network), fall back
            # to a local CSV writer so the pipeline can continue in offline mode.
            logger.warning(f"Google Sheets unavailable, falling back to local storage: {e}")
            self._disabled = True
            self._local_path = 'downloads/raw_feed_backup.csv'
            # Ensure downloads dir exists
            try:
                import os
                os.makedirs('downloads', exist_ok=True)
            except Exception:
                pass

    def get_existing_ids(self):
        if getattr(self, '_disabled', False):
            # Read first column from local CSV if present
            try:
                import csv
                ids = set()
                with open(self._local_path, newline='', encoding='utf-8') as fh:
                    reader = csv.reader(fh)
                    for row in reader:
                        if row:
                            ids.add(row[0])
                logger.info(f"(local) Загружено {len(ids)} существующих id из {self._local_path}")
                return ids
            except FileNotFoundError:
                return set()
            except Exception as e:
                logger.error(f"Ошибка чтения локального CSV: {e}")
                return set()
        try:
            ids = set(self.sheet.col_values(1)[1:])
            logger.info(f"Загружено {len(ids)} существующих id из Google Sheets")
            return ids
        except Exception as e:
            logger.error(f"Ошибка получения id: {e}")
            return set()

    def get_existing_checksums(self):
        if getattr(self, '_disabled', False):
            try:
                import csv
                checksums = []
                with open(self._local_path, newline='', encoding='utf-8') as fh:
                    reader = csv.reader(fh)
                    for row in reader:
                        if len(row) >= 13:
                            checksums.append(row[12])
                logger.info(f"(local) Загружено {len(checksums)} существующих checksum из {self._local_path}")
                return set(checksums)
            except FileNotFoundError:
                return set()
            except Exception as e:
                logger.error(f"Ошибка чтения локального CSV: {e}")
                return set()
        try:
            checksums = self.sheet.col_values(13)[1:]
            logger.info(f"Загружено {len(checksums)} существующих checksum из Google Sheets")
            return set(checksums)
        except Exception as e:
            logger.error(f"Ошибка получения checksum: {e}")
            return set()

    def append_news_batch(self, news_items):
        rows = [
            [
                n.id, n.source_type, n.source_name, n.source_url, n.created_at, n.ingest_status,
                n.raw_title, n.raw_content, n.raw_html, n.raw_media, n.lang, n.raw_tags,
                n.checksum, n.parse_error, n.debug_info
            ] for n in news_items
        ]
        if getattr(self, '_disabled', False):
            try:
                import csv
                with open(self._local_path, 'a', newline='', encoding='utf-8') as fh:
                    writer = csv.writer(fh)
                    for r in rows:
                        writer.writerow(r)
                logger.info(f"(local) Добавлено {len(rows)} новых новостей в {self._local_path}")
                return
            except Exception as e:
                logger.error(f"Ошибка записи в локальный CSV: {e}")
                return
        try:
            self.sheet.append_rows(rows, value_input_option="RAW")
            logger.info(f"Добавлено {len(rows)} новых новостей в Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка при добавлении новостей: {e}")
