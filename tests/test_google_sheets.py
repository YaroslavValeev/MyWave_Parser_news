import unittest
from storage.google_sheets import GoogleSheets
from unittest.mock import MagicMock
from utils.sheet_schema import RAW_FEED_COLUMNS

class TestGoogleSheets(unittest.TestCase):
    def test_append_news(self):
        gs = GoogleSheets.__new__(GoogleSheets)
        gs.sheet = MagicMock()
        with self.assertRaises(RuntimeError):
            gs.append_news(["позиционная", "запись", "запрещена"])

    def test_append_news_batch(self):
        gs = GoogleSheets.__new__(GoogleSheets)
        gs.sheet = MagicMock()

        header = RAW_FEED_COLUMNS
        row_number_idx = header.index("row_number")

        # Имитируем пустой лист с заголовками (1 строка = header)
        gs.sheet.get_all_values.return_value = [header]
        gs.sheet.row_values.return_value = header

        items = [{
            "id": "1",
            "source_type": "rss",
            "source_name": "test",
            "source_url": "https://example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "ingest_status": "ok",
            "raw_title": "title",
            "raw_content": "content",
            "checksum": "abc",
        }]

        gs.append_news_batch(items)
        self.assertTrue(gs.sheet.append_rows.called, "Ошибка при пакетной записи в Google Sheets")

        # Проверяем, что row_number проставлен детерминированно (следующая строка после заголовков = 2)
        args, kwargs = gs.sheet.append_rows.call_args
        appended_rows = args[0]
        self.assertEqual(appended_rows[0][row_number_idx], "2")

if __name__ == "__main__":
    unittest.main()