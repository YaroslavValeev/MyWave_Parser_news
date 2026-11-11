"""The private-delivery probe never writes production records or resends a receipt."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from config.settings import config
from storage.repository import AsyncNewsRepository, initialize_database
from utils.source_context import source_input_hash

SPEC = importlib.util.spec_from_file_location(
    "live_final_probe",
    Path(__file__).resolve().parents[1] / "scripts/probe_live_final_post.py",
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
URL = "https://news.example.test/news/1786"
PRIVATE_ID = 9001


@pytest_asyncio.fixture
async def source(tmp_path, monkeypatch):
    db = tmp_path / "production.db"
    await initialize_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO items(id,source,title,content,link,status,checksum,images) VALUES (626,?,?,?,?,?,?,?)",
            (
                "test",
                "Зимнее хранение двигателя",
                "Подробнее: " + URL,
                "https://t.me/example/626",
                "review",
                "selected",
                "https://cdn.example.test/cover.jpg",
            ),
        )
        connection.execute(
            "INSERT INTO items(id,source,content,status,checksum) VALUES(649,'test','rejected','discarded','rejected')"
        )
    repo = AsyncNewsRepository(db)
    item = await repo.get_item(626)
    article = "В статье описана подготовка двигателя к зимнему хранению. " * 10
    evidence = {
        "version": 1,
        "requested_url": URL,
        "final_url": URL,
        "title": "Подготовка двигателя к зиме",
        "text": article,
        "input_sha256": source_input_hash(item),
        "text_sha256": hashlib.sha256(article.encode()).hexdigest(),
    }
    await repo.save_source_context(626, evidence)
    await repo.save_nlp_results(
        626,
        summary="Статья посвящена подготовке двигателя к зимнему хранению, обслуживанию топливной системы и аккумуляторов.",
        extra={
            "source_input_sha256": evidence["input_sha256"],
            "source_text_sha256": evidence["text_sha256"],
        },
    )
    monkeypatch.setattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", True)
    monkeypatch.setattr(config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("news.example.test",))
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "offline-test")
    monkeypatch.setattr(PROBE, "RECEIPTS", tmp_path / "receipts")
    # Any accidentally re-entered site/Sheets/model transport is a regression.
    forbidden = AsyncMock(side_effect=AssertionError("external_transport_forbidden"))
    monkeypatch.setattr("services.publication.sync_publication_result", forbidden)
    monkeypatch.setattr("nlp.openai_client.get_openai_client", forbidden)
    yield db
    forbidden.assert_not_awaited()


def bot_mock(monkeypatch, *, chat_type="private", fail=False):
    async def send(**kwargs):
        if fail:
            raise TimeoutError("unknown_delivery")
        body = "".join(PROBE.PlainHTML(kwargs["text"]).parts)
        return SimpleNamespace(
            chat=SimpleNamespace(id=kwargs["chat_id"]), message_id=1234, text=body
        )

    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=PRIVATE_ID, type=chat_type)),
        send_message=AsyncMock(side_effect=send),
        send_photo=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(PROBE, "build_bot", lambda: bot)
    return bot


def test_runtime_hashes_match_editor_and_reject_drift_before_api(tmp_path, monkeypatch):
    from scripts.audit_reconciled_runtime import EXPECTED

    assert PROBE.RUNTIME_HASHES == EXPECTED and len(EXPECTED) == 106
    monkeypatch.setattr(
        PROBE, "RUNTIME_HASHES", {"editor.py": hashlib.sha256(b"ready=1\n").hexdigest()}
    )
    with pytest.raises(RuntimeError, match="manual_editor_not_installed"):
        PROBE.preflight_runtime(tmp_path)
    (tmp_path / "editor.py").write_bytes(b"ready=1\r\n")
    PROBE.preflight_runtime(tmp_path)
    (tmp_path / "editor.py").write_bytes(b"old_editor=1\n")
    with pytest.raises(RuntimeError, match="manual_editor_not_installed"):
        PROBE.preflight_runtime(tmp_path)


@pytest.mark.asyncio
async def test_check_is_read_only_without_bot_or_receipts(source, monkeypatch, capsys):
    before = source.read_bytes()
    monkeypatch.setattr(
        PROBE, "build_bot", lambda: pytest.fail("check must not build Telegram session")
    )
    await PROBE.probe(source, PRIVATE_ID, 626, URL)
    report = json.loads(capsys.readouterr().out)
    assert report["live_probe"] == "check_ok" and report["telegram_sends"] == 0
    assert source.read_bytes() == before and not PROBE.RECEIPTS.exists()


@pytest.mark.asyncio
async def test_delivers_manual_body_once_and_preserves_production_database(
    source, monkeypatch, capsys
):
    before = source.read_bytes()
    bot = bot_mock(monkeypatch)
    await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    report = json.loads(capsys.readouterr().out)
    assert report["live_probe"] == "ok" and report["manual_text_verified"] is True
    assert (
        report["production_records_unchanged"] is True and report["message_id"] == 1234
    )
    assert report["production_database_writes"] == report["real_openai_requests"] == 0
    assert source.read_bytes() == before
    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_not_awaited()
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == PRIVATE_ID and sent["request_timeout"] == 25
    assert "ТЕСТ MyWave" in sent["text"] and "сохранённая ручная версия" in sent["text"]
    assert "Тестовая проверка ручного редактора и доставки" not in sent["text"]
    assert bot.session.close.await_count == 1
    await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    duplicate = json.loads(capsys.readouterr().out)
    assert (
        duplicate["live_probe"] == "already_sent"
        and duplicate["telegram_sends_this_run"] == 0
    )
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_delivery_is_not_retried(source, monkeypatch):
    before = source.read_bytes()
    bot = bot_mock(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="probe_not_delivered"):
        await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    bot.send_message.assert_awaited_once()
    bot.session.close.assert_awaited_once()
    assert source.read_bytes() == before
    receipt = next(PROBE.RECEIPTS.glob("*.json"))
    assert json.loads(receipt.read_text())["state"] == "send_started"
    with pytest.raises(RuntimeError, match="previous_send_requires_inspection"):
        await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_chat_type_is_blocked_before_any_send(source, monkeypatch):
    bot = bot_mock(monkeypatch, chat_type="channel")
    with pytest.raises(RuntimeError, match="target_is_not_requested_private_chat"):
        await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    bot.send_message.assert_not_awaited()
    assert not PROBE.RECEIPTS.exists()
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "binding", "publication"])
async def test_changed_production_source_cannot_be_sent(source, monkeypatch, change):
    bot = bot_mock(monkeypatch)
    with sqlite3.connect(source) as db:
        if change == "status":
            db.execute("UPDATE items SET status='discarded' WHERE id=626")
        elif change == "binding":
            db.execute("UPDATE items SET content='Другой источник' WHERE id=626")
        else:
            db.execute(
                "INSERT INTO publications(item_id,channel_id,message_id) VALUES(626,'offline','1')"
            )
    with pytest.raises(RuntimeError):
        await PROBE.probe(source, PRIVATE_ID, 626, URL, send=True)
    bot.get_chat.assert_not_awaited()
    bot.send_message.assert_not_awaited()
