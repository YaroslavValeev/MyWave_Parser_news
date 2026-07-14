import unittest

from utils.import_asyncio import save_to_sheet
from utils.sheet_schema import RAW_FEED_COLUMNS


class _FakeWorksheet:
    def __init__(self):
        self._header = list(RAW_FEED_COLUMNS)
        self.appended_rows = []
        self.batch_updates = []

    def get_all_values(self):
        return [self._header]

    def row_values(self, row_index):
        return self._header if row_index == 1 else []

    def append_row(self, row):
        self._header = list(row)

    def append_rows(self, rows, value_input_option="RAW"):
        self.appended_rows.extend(rows)
        if not rows:
            return {"updates": {"updatedRange": "raw_feed!A2:BP2"}}
        start_row = 2
        end_row = start_row + len(rows) - 1
        return {"updates": {"updatedRange": f"raw_feed!A{start_row}:BP{end_row}"}}

    def batch_update(self, updates, value_input_option="RAW"):
        self.batch_updates.extend(updates)


class TestRawFeedPublishContract(unittest.IsolatedAsyncioTestCase):
    async def test_drop_publishable_without_final_posts(self):
        ws = _FakeWorksheet()
        item = {
            "id": "x1",
            "source_type": "telegram",
            "source_name": "test",
            "source_item_id": "m1",
            "source": "telegram",
            "title": "t",
            "source_url": "https://t.me/test/1",
            "raw_title": "t",
            "raw_content": "c",
            "status": "PUBLISHED",
            "checksum": "cs1",
            "final_posts": "",
        }

        await save_to_sheet(None, "raw_feed", [item], existing_checksums=set(), ws_cache=ws)
        self.assertEqual(len(ws.appended_rows), 0)
        self.assertIn("publishable_status_requires_final_posts", item.get("error_log", ""))
        self.assertIn("drop_reason=publishable_status_requires_final_posts", item.get("debug_info", ""))

    async def test_allow_publishable_with_final_posts(self):
        ws = _FakeWorksheet()
        item = {
            "id": "x2",
            "source_type": "telegram",
            "source_name": "test",
            "source_item_id": "m2",
            "source": "telegram",
            "title": "t",
            "source_url": "https://t.me/test/2",
            "raw_title": "t",
            "raw_content": "c",
            "status": "READY_TO_PUBLISH",
            "checksum": "cs2",
            "final_posts": "Финальный подтверждённый текст",
        }

        await save_to_sheet(None, "raw_feed", [item], existing_checksums=set(), ws_cache=ws)
        self.assertEqual(len(ws.appended_rows), 1)


if __name__ == "__main__":
    unittest.main()
