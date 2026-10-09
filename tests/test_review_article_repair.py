import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repair_review_article_626.py"
SPEC = importlib.util.spec_from_file_location("review_article_repair", SCRIPT)
REPAIR = importlib.util.module_from_spec(SPEC)
disabled = logging.root.manager.disable
SPEC.loader.exec_module(REPAIR)
logging.disable(disabled)
TEXT = (
    "Материал о зимнем хранении Malibu Monsoon M5/M6 и ограничении для части M6Di. " * 8
)


def rows(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return {
            name: [
                dict(row) for row in db.execute("SELECT * FROM " + name + " ORDER BY 1")
            ]
            for name in ("items", "nlp_results", "publications", "logs")
        }


def evidence(item):
    return {
        "version": 1,
        "requested_url": REPAIR.URL,
        "final_url": REPAIR.URL,
        "title": REPAIR.TITLE,
        "text": TEXT,
        "input_sha256": REPAIR.source_input_hash(item),
        "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(REPAIR.config, "TEXT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(REPAIR.config, "SOURCE_ARTICLE_FETCH_ENABLED", True)
    monkeypatch.setattr(REPAIR.config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("wakeflot.ru",))
    monkeypatch.setattr(
        REPAIR, "TEXT_SHA256", hashlib.sha256(TEXT.encode()).hexdigest()
    )
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY,status TEXT,content TEXT,transcript TEXT,link TEXT,source_context TEXT,images TEXT,updated_at TEXT);"
            "CREATE TABLE nlp_results(id INTEGER PRIMARY KEY,item_id INTEGER UNIQUE,summary TEXT,questions TEXT,decision TEXT,moderation TEXT,extra TEXT,author_notes TEXT,merged_text TEXT,voice_file TEXT,rewrite_guidance TEXT,version INTEGER,updated_at TEXT);"
            "CREATE TABLE publications(id INTEGER PRIMARY KEY,item_id INTEGER,channel_id TEXT);"
            "CREATE TABLE logs(id INTEGER PRIMARY KEY,item_id INTEGER,level TEXT,message TEXT,meta TEXT,created_at TEXT);"
        )
        db.executemany(
            "INSERT INTO items VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    626,
                    "review",
                    f"Подробнее [{REPAIR.URL}]({REPAIR.URL})",
                    None,
                    "https://t.me/wakeflot/3043",
                    None,
                    "cover.jpg",
                    "original",
                ),
                (
                    649,
                    "discarded",
                    "https://wakeflot.ru/news/1785",
                    None,
                    "https://t.me/wakeflot/3048",
                    None,
                    "old.jpg",
                    "rejected",
                ),
                (
                    777,
                    "published",
                    "Другой материал",
                    None,
                    "https://example.com/other",
                    None,
                    "other.jpg",
                    "other",
                ),
            ],
        )
        db.executemany(
            "INSERT INTO nlp_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    1,
                    626,
                    "Чемпионат вместо консервации",
                    '["Старый вопрос"]',
                    "publish",
                    "old moderation",
                    json.dumps(
                        {
                            "cover": {"url": "cover.jpg"},
                            "custom": {"keep": True},
                            "source_context_missing": True,
                            "owner_editing_text": "Старый перевод",
                            "owner_display_title": "Старый заголовок",
                            "event_id": "old_event",
                        }
                    ),
                    "Комментарий владельца",
                    None,
                    "voice.ogg",
                    "Редакторские пожелания",
                    4,
                    "before",
                ),
                (
                    2,
                    649,
                    "Нет текста источника",
                    "[]",
                    "review",
                    None,
                    "{}",
                    None,
                    None,
                    None,
                    None,
                    2,
                    "protected",
                ),
                (
                    3,
                    777,
                    "Другая новость",
                    "[]",
                    "publish",
                    None,
                    "{}",
                    "Другой комментарий",
                    "Другой готовый текст",
                    None,
                    None,
                    1,
                    "other",
                ),
            ],
        )
        db.execute("INSERT INTO publications VALUES(1,777,'published-channel')")
    return path, tmp_path / "backups"


@pytest.mark.asyncio
async def test_check_reads_only_and_creates_no_backup(setup):
    path, backups = setup
    original = path.read_bytes()
    result = await REPAIR.repair(
        path,
        backups,
        retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
    )
    assert result["review_repair"] == "ready" and result["database_writes"] == 0
    assert path.read_bytes() == original and not backups.exists()


@pytest.mark.asyncio
async def test_apply_restores_grounding_preserves_owner_media_and_other_items_then_rolls_back(
    setup,
):
    from utils.item_context import get_item_text_context, is_title_only_summary_fallback
    from utils.owner_content import owner_editing_text

    path, backups = setup
    before = rows(path)
    result = await REPAIR.repair(
        path,
        backups,
        apply=True,
        retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
    )
    after = rows(path)
    item = after["items"][0]
    nlp = {
        **after["nlp_results"][0],
        "extra": json.loads(after["nlp_results"][0]["extra"]),
    }
    assert item["status"] == "review" and nlp["decision"] == "review"
    assert get_item_text_context(item) == owner_editing_text(item, nlp) == TEXT
    assert not is_title_only_summary_fallback(item, nlp)
    assert {key: value for key, value in item.items() if key != "source_context"} == {
        key: value
        for key, value in before["items"][0].items()
        if key != "source_context"
    }
    assert after["items"][1:] == before["items"][1:]
    assert after["nlp_results"][1:] == before["nlp_results"][1:]
    assert after["publications"] == before["publications"]
    for key in ("author_notes", "voice_file", "rewrite_guidance", "merged_text"):
        assert nlp[key] == before["nlp_results"][0][key]
    assert nlp["extra"]["cover"] == {"url": "cover.jpg"} and nlp["extra"]["custom"] == {
        "keep": True
    }
    assert (
        nlp["questions"] == "[]" and nlp["moderation"] is None and nlp["version"] == 5
    )
    assert result["telegram_sends"] == result["real_openai_requests"] == 0
    second = await REPAIR.repair(
        path,
        backups,
        apply=True,
        retriever=AsyncMock(side_effect=AssertionError("must not fetch twice")),
    )
    assert second["review_repair"] == "already_done" and rows(path) == after
    rolled_back = REPAIR.rollback(path, backups, result["backup"])
    restored = rows(path)
    assert rolled_back["review_repair"] == "rolled_back"
    assert all(
        restored[key] == before[key] for key in ("items", "nlp_results", "publications")
    )
    assert [row["message"] for row in restored["logs"]] == [
        "review_article_nlp_repaired",
        "review_article_nlp_rolled_back",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["new", "deferred", "approved", "published", "discarded", "processing"]
)
async def test_refuses_ineligible_status_before_fetch(setup, status):
    path, backups = setup
    with sqlite3.connect(path) as db:
        db.execute("UPDATE items SET status=? WHERE id=626", (status,))
    original = path.read_bytes()
    fetch = AsyncMock()
    with pytest.raises(REPAIR.RepairRefused, match="target_status_changed"):
        await REPAIR.repair(path, backups, apply=True, retriever=fetch)
    assert path.read_bytes() == original and not backups.exists()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_field", ["merged_text", "owner_rewritten"])
async def test_preserves_owner_final_text_and_refuses_overwrite(setup, owner_field):
    path, backups = setup
    with sqlite3.connect(path) as db:
        if owner_field == "merged_text":
            db.execute(
                "UPDATE nlp_results SET merged_text='Готовый авторский текст' WHERE item_id=626"
            )
        else:
            db.execute(
                "UPDATE nlp_results SET extra=? WHERE item_id=626",
                (json.dumps({"owner_rewritten": True}),),
            )
    original = path.read_bytes()
    fetch = AsyncMock()
    with pytest.raises(REPAIR.RepairRefused, match="owner_final_text_present"):
        await REPAIR.repair(path, backups, apply=True, retriever=fetch)
    assert path.read_bytes() == original and not backups.exists()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("item_id", [626, 649])
async def test_existing_publication_blocks_repair(setup, item_id):
    path, backups = setup
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO publications(item_id,channel_id) VALUES(?,'already-sent')",
            (item_id,),
        )
    before = rows(path)
    with pytest.raises(REPAIR.RepairRefused, match="target_has_publications"):
        await REPAIR.repair(path, backups, apply=True, retriever=AsyncMock())
    assert rows(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["author_notes", "images", "content"])
@pytest.mark.parametrize("apply", [False, True])
async def test_compare_all_target_fields_after_fetch_and_preserve_concurrent_changes(
    setup, field, apply
):
    path, backups = setup

    async def fetch(item, hosts):
        with sqlite3.connect(path) as db:
            table, key = (
                ("nlp_results", "item_id")
                if field == "author_notes"
                else ("items", "id")
            )
            db.execute(
                f"UPDATE {table} SET {field}='concurrent owner change' WHERE {key}=626"
            )
        return evidence(item)

    with pytest.raises(REPAIR.RepairRefused, match="target_changed_during_fetch"):
        await REPAIR.repair(path, backups, apply=apply, retriever=fetch)
    after = rows(path)
    assert after["nlp_results"][0]["summary"] == "Чемпионат вместо консервации"
    assert not backups.exists()
    table = "nlp_results" if field == "author_notes" else "items"
    assert after[table][0][field] == "concurrent owner change"


@pytest.mark.asyncio
async def test_changed_or_tampered_article_never_writes(setup, monkeypatch):
    path, backups = setup
    original = path.read_bytes()
    for bad in ("invalid_hash", "changed_article", "changed_title"):

        async def fetch(item, hosts):
            value = evidence(item)
            if bad == "invalid_hash":
                return {**value, "text_sha256": "tampered"}
            if bad == "changed_title":
                return {**value, "title": "Другой заголовок"}
            text = "Изменённая статья о другом моторе. " * 20
            return {
                **value,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }

        with pytest.raises(REPAIR.RepairRefused, match="article_evidence_changed"):
            await REPAIR.repair(path, backups, apply=True, retriever=fetch)
    assert path.read_bytes() == original and not backups.exists()


@pytest.mark.asyncio
async def test_backup_failure_after_updates_rolls_back_records_and_audit(
    setup, monkeypatch
):
    path, backups = setup
    before = rows(path)
    write = REPAIR.write_json

    def fail_after(path, value):
        if path.name == "after.json":
            raise OSError("backup storage unavailable")
        write(path, value)

    monkeypatch.setattr(REPAIR, "write_json", fail_after)
    with pytest.raises(OSError):
        await REPAIR.repair(
            path,
            backups,
            apply=True,
            retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
        )
    assert rows(path) == before


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_both_records(setup, monkeypatch):
    path, backups = setup
    before = rows(path)

    def fail(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(REPAIR, "audit", fail)
    with pytest.raises(OSError):
        await REPAIR.repair(
            path,
            backups,
            apply=True,
            retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
        )
    assert rows(path) == before


@pytest.mark.asyncio
async def test_rollback_refuses_new_owner_edits_and_tampered_backup(setup):
    path, backups = setup
    result = await REPAIR.repair(
        path,
        backups,
        apply=True,
        retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE nlp_results SET author_notes='Новый комментарий' WHERE item_id=626"
        )
    before = rows(path)
    with pytest.raises(REPAIR.RepairRefused, match="rollback_target_changed"):
        REPAIR.rollback(path, backups, result["backup"])
    assert rows(path) == before
    state = Path(result["backup"]) / "state.json"
    state.write_text("{}")
    with pytest.raises(REPAIR.RepairRefused, match="backup_changed"):
        REPAIR.rollback(path, backups, result["backup"])
    assert rows(path) == before
