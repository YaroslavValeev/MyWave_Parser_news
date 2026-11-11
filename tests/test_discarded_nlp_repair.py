import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repair_discarded_nlp_result.py"
SPEC = importlib.util.spec_from_file_location("discarded_nlp_repair", SCRIPT)
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)


def database(path):
    db = sqlite3.connect(path)
    try:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY,status TEXT,content TEXT,transcript TEXT,updated_at TEXT);"
            "CREATE TABLE nlp_results(item_id INTEGER PRIMARY KEY,summary TEXT,questions TEXT,decision TEXT,"
            "moderation TEXT,extra TEXT,merged_text TEXT,author_notes TEXT,voice_file TEXT,rewrite_guidance TEXT,"
            "version INTEGER,updated_at TEXT);"
            "CREATE TABLE publications(item_id INTEGER);"
        )
        db.executemany(
            "INSERT INTO items VALUES(?,?,?,?,?)",
            [
                (
                    649,
                    "discarded",
                    "https://wakeflot.ru/news/1785",
                    "",
                    "owner_rejected",
                ),
                (650, "new", "Другой источник", "", "unchanged"),
            ],
        )
        db.executemany(
            "INSERT INTO nlp_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    649,
                    "Чемпионат 2023",
                    "[]",
                    "publish",
                    "old moderation",
                    json.dumps(
                        {
                            "cover": {"url": "cover.jpg"},
                            "owner_editing_text": "Чемпионат 2023",
                            "translated_text": "Чемпионат 2023",
                            "owner_display_title": "Чемпионат",
                            "event_id": "wrong_event",
                            "owner_rewritten": False,
                        }
                    ),
                    "Чемпионат 2023 финал",
                    "Комментарий владельца",
                    "voice.ogg",
                    "Правка владельца",
                    4,
                    "before",
                ),
                (
                    650,
                    "Верный текст",
                    "[]",
                    "review",
                    None,
                    "{}",
                    None,
                    None,
                    None,
                    None,
                    1,
                    "before",
                ),
            ],
        )
        db.commit()
    finally:
        db.close()


def snapshot(path):
    db = sqlite3.connect(path)
    try:
        return {
            name: db.execute("SELECT * FROM " + name).fetchall()
            for name in ("items", "nlp_results", "publications")
        }
    finally:
        db.close()


def test_redaction_preserves_rejection_owner_media_and_other_records(tmp_path):
    path = tmp_path / "data.db"
    database(path)
    before = snapshot(path)
    result = REPAIR.repair(path, tmp_path / "backups", apply=True)
    after = snapshot(path)
    assert result["repair"] == "ok" and result["status_preserved"] == "discarded"
    assert (
        after["items"] == before["items"]
        and after["publications"] == before["publications"]
    )
    assert after["nlp_results"][1] == before["nlp_results"][1]
    saved = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
    assert saved["nlp"]["summary"] == "Чемпионат 2023"
    row = after["nlp_results"][0]
    assert "нет текстового контента" in row[1] and row[3] == "review"
    assert row[4] is None and row[6] is None
    assert row[7:10] == before["nlp_results"][0][7:10] and row[10] == 5
    extra = json.loads(row[5])
    assert (
        extra["cover"] == {"url": "cover.jpg"}
        and extra["source_context_missing"] is True
    )
    assert (
        not {"event_id", "translated_text", "owner_editing_text", "owner_display_title"}
        & extra.keys()
    )
    assert (
        REPAIR.repair(path, tmp_path / "backups", apply=True)["repair"]
        == "already_done"
    )
    assert snapshot(path) == after


def test_dry_run_does_not_change_rows_or_create_backup(tmp_path):
    path = tmp_path / "data.db"
    database(path)
    before = snapshot(path)
    assert REPAIR.repair(path, tmp_path / "backups")["repair"] == "ready"
    assert snapshot(path) == before and not (tmp_path / "backups").exists()


@pytest.mark.parametrize(
    "change,reason",
    [
        ("UPDATE items SET status='review' WHERE id=649", "target_status_changed"),
        ("INSERT INTO publications VALUES(649)", "target_has_publications"),
        (
            "UPDATE items SET transcript='Текст источника' WHERE id=649",
            "target_has_source_text",
        ),
        (
            "UPDATE items SET content='https://wakeflot.ru/news/9999' WHERE id=649",
            "target_source_changed",
        ),
        (
            "UPDATE nlp_results SET extra='{\"owner_rewritten\":true}' WHERE item_id=649",
            "target_owner_rewrite_changed",
        ),
    ],
)
def test_changed_state_refuses_before_any_write(tmp_path, change, reason):
    path = tmp_path / "data.db"
    database(path)
    db = sqlite3.connect(path)
    try:
        db.execute(change)
        db.commit()
    finally:
        db.close()
    before = snapshot(path)
    with pytest.raises(REPAIR.RepairRefusal, match=reason):
        REPAIR.repair(path, tmp_path / "backups", apply=True)
    assert snapshot(path) == before and not (tmp_path / "backups").exists()
