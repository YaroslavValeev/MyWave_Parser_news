import unittest
from storage.google_sheets import GoogleSheets
from unittest.mock import MagicMock
from core.models import NewsItem

class TestGoogleSheets(unittest.TestCase):
    def setUp(self):
        self.gs = GoogleSheets()

    def test_append_news(self):
        test_data = ["123", "Тестовая новость", "Тестовый текст", "https://example.com"]
        try:
            self.gs.append_news(test_data)
            result = True
        except Exception:
            result = False
        self.assertTrue(result, "Ошибка при записи в Google Sheets")

    def test_append_news_batch(self):
        gs = GoogleSheets.__new__(GoogleSheets)
        gs.sheet = MagicMock()
        items = [NewsItem(
            id="1", source_type="rss", source_name="test", source_url="url",
            created_at="now", raw_title="title", raw_content="content"
        )]
        gs.append_news_batch(items)
        self.assertTrue(gs.sheet.append_rows.called, "Ошибка при пакетной записи в Google Sheets")

if __name__ == "__main__":
    unittest.main()