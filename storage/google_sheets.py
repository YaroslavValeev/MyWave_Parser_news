import gspread
from google.oauth2.service_account import Credentials
from config.settings import config
import logging

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
            self.sheet = self.client.open_by_key(config.GOOGLE_SHEET_ID).sheet1
            logger.info("Google Sheets connected successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to Google Sheets: {e}")
            self.sheet = None

    def append_news(self, news):
        """Добавляет новость в Google Sheets с защитой от сбоев"""
        if not self.sheet:
            logger.error("Google Sheets не настроен. Запись невозможна.")
            return
        try:
            self.sheet.append_row(news)
            logger.info("Новость успешно добавлена в Google Sheets.")
        except Exception as e:
            logger.error(f"Ошибка записи в Google Sheets: {e}")

# Пример использования:
# gs = GoogleSheets()
# gs.append_news(["1", "Заголовок", "Текст новости", "https://source.com"])
