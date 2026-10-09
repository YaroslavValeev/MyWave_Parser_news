"""Экспорт owner_* в CSV fallback (без реального Google Sheets)."""

import pytest

from config.settings import config
from services.owner_audit_export import run_owner_audit_export
from storage.repository import AsyncNewsRepository, initialize_database


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_audit_cursor_and_fetch_owner_logs(tmp_path):
    db_file = tmp_path / "a.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)

    assert await repo.get_audit_export_cursor("owner_actions_last_log_id") == 0

    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "t",
            "content": "c",
            "link": "https://ex.example/x",
        }
    )
    await repo.log_event(item_id, "info", "owner_approve", {"user_id": 42, "username": "u"})
    await repo.log_event(item_id, "info", "other", {})

    rows = await repo.fetch_owner_action_logs_after(0, limit=10)
    assert len(rows) == 1
    assert rows[0]["message"] == "owner_approve"
    assert rows[0]["meta"].get("user_id") == 42

    await repo.set_audit_export_cursor("owner_actions_last_log_id", rows[0]["id"])
    assert await repo.get_audit_export_cursor("owner_actions_last_log_id") == rows[0]["id"]


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_run_owner_audit_export_writes_csv_without_sheets(tmp_path, monkeypatch):
    db_file = tmp_path / "db.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", None)

    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    iid = await repo.create_item(
        {"source": "t", "title": "t", "content": "c", "link": "https://z.example"}
    )
    await repo.log_event(iid, "info", "owner_discard", {"user_id": 7})

    result = await run_owner_audit_export(repo)
    assert result.exported == 1
    assert result.sheets_written is False
    assert result.csv_path
    assert tmp_path.joinpath("exports").exists() or "exports" in (result.csv_path or "")
    assert result.error is None
    assert await repo.get_audit_export_cursor("owner_actions_last_log_id") == result.new_cursor
