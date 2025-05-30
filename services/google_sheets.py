import gspread
from oauth2client.service_account import ServiceAccountCredentials
from core.models import NewsItem
from core.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GoogleSheets:
    def __init__(self):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(settings.google_credentials_path, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(settings.google_sheet_id).worksheet("raw_feed")
            logger.info(f"Google Sheets подключен: {settings.google_sheet_id} [raw_feed]")
        except Exception as e:
            logger.error(f"Ошибка инициализации Google Sheets: {e}")
            raise

    def get_existing_ids(self):
        try:
            ids = set(self.sheet.col_values(1)[1:])
            logger.info(f"Загружено {len(ids)} существующих id из Google Sheets")
            return ids
        except Exception as e:
            logger.error(f"Ошибка получения id: {e}")
            return set()

    def get_existing_checksums(self):
        try:
            checksums = self.sheet.col_values(13)[1:]
            logger.info(f"Загружено {len(checksums)} существующих checksum из Google Sheets")
            return set(checksums)
        except Exception as e:
            logger.error(f"Ошибка получения checksum: {e}")
            return set()

    def append_news_batch(self, news_items):
        try:
            rows = [
                [
                    n.id, n.source_type, n.source_name, n.source_url, n.created_at, n.ingest_status,
                    n.raw_title, n.raw_content, n.raw_html, n.raw_media, n.lang, n.raw_tags,
                    n.checksum, n.parse_error, n.debug_info
                ] for n in news_items
            ]
            self.sheet.append_rows(rows, value_input_option="RAW")
            logger.info(f"Добавлено {len(rows)} новых новостей в Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка при добавлении новостей: {e}")
