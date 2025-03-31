import unittest
from storage.google_sheets import GoogleSheets

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

if __name__ == "__main__":
    unittest.main()