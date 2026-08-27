import pytest

from utils import sheet_gateway


class FakeWorksheet:
    def __init__(self, header):
        self.header = list(header)
        self.rows = [self.header]
        self.col_count = len(self.header)
        self.updated_headers = []
        self.appended_rows = []

    def row_values(self, row):
        return list(self.rows[row - 1]) if row <= len(self.rows) else []

    def append_row(self, values, value_input_option=None):
        self.header = list(values)
        self.rows = [self.header]
        self.col_count = len(self.header)

    def resize(self, cols=None):
        if cols:
            self.col_count = cols

    def update(self, range_name=None, values=None, value_input_option=None):
        self.header = list(values[0])
        self.rows[0] = self.header
        self.updated_headers.append(self.header)

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def append_rows(self, rows, value_input_option=None):
        self.appended_rows.extend(rows)
        self.rows.extend(rows)


@pytest.mark.asyncio
async def test_append_raw_feed_rows_appends_missing_headers_without_reordering(monkeypatch):
    existing_header = ["id", "checksum", "cover_image_url", "published_at", "source_item_id"]
    ws = FakeWorksheet(existing_header)
    monkeypatch.setattr(sheet_gateway, "get_worksheet", lambda doc, sheet_name: ws)
    monkeypatch.setattr(sheet_gateway, "validate_raw_row", lambda row: True)

    written = await sheet_gateway.append_raw_feed_rows(
        object(),
        [
            {
                "id": "1",
                "checksum": "cs-1",
                "source_type": "rss",
                "source_name": "Test",
                "source_item_id": "source-1",
                "raw_title": "Title",
                "raw_content": "Body",
            }
        ],
    )

    assert written == 1
    assert ws.updated_headers
    assert ws.updated_headers[-1][: len(existing_header)] == existing_header
    assert ws.header.index("image_url") > ws.header.index("source_item_id")

    row = ws.appended_rows[0]
    assert row[ws.header.index("source_item_id")] == "source-1"
    assert row[ws.header.index("published_at")] == ""
