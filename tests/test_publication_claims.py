"""Real SQLite races and delivery ambiguity use fake transports, never Telegram."""

import asyncio
import sqlite3
import importlib.util
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage

from services.publication import (
    PublicationService,
    PublicationSendError,
    _send_with_retry,
)
from storage.repository import AsyncNewsRepository, initialize_database


@pytest_asyncio.fixture
async def database(tmp_path):
    path = tmp_path / "claims.db"
    await initialize_database(path)
    repo = AsyncNewsRepository(path)
    item = await repo.create_item(
        {"source": "test", "content": "Facts", "status": "approved"}
    )
    await repo.save_nlp_results(item, summary="Facts")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE nlp_results SET author_notes='Owner note' WHERE item_id=?", (item,)
        )
    return path, item


def stored(path, sql, args=()):
    with sqlite3.connect(path) as db:
        return db.execute(sql, args).fetchall()


def service(path, bot, monkeypatch):
    repo = AsyncNewsRepository(path)
    svc = PublicationService(repo, bot, channel_id="42")
    monkeypatch.setattr(svc, "_publication_quality_issues", lambda *args: [])
    monkeypatch.setattr(svc, "_build_caption", lambda *args: "Facts and owner note")
    monkeypatch.setattr(svc, "_hydrate_media", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "_handle_success", AsyncMock())
    monkeypatch.setattr(
        "services.publication.is_title_only_summary_fallback", lambda *args: False
    )
    monkeypatch.setattr(
        "services.publication.ensure_merged_owner_post", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        "services.editorial_layers.publication_allowed", lambda *args: (True, None)
    )
    return svc


@pytest.mark.asyncio
async def test_only_one_connection_wins_and_restart_cannot_reclaim(database):
    path, item = database
    a, b = AsyncNewsRepository(path), AsyncNewsRepository(path)
    results = await asyncio.gather(
        a.claim_publication(item, "42"), b.claim_publication(item, "42")
    )
    assert sum(token is not None for token in results) == 1
    assert await AsyncNewsRepository(path).claim_publication(item, "99") is None


@pytest.mark.asyncio
async def test_legacy_receipt_or_discarded_item_cannot_be_claimed(database):
    path, item = database
    repo = AsyncNewsRepository(path)
    await repo.save_publication(item, "42", "10")
    assert await repo.claim_publication(item, "42") is None
    other = await repo.create_item(
        {"source": "test", "content": "Other", "status": "discarded"}
    )
    assert await repo.claim_publication(other, "42") is None


@pytest.mark.asyncio
async def test_complete_commits_receipt_status_claim_and_log_together(database):
    path, item = database
    repo = AsyncNewsRepository(path)
    token = await repo.claim_publication(item, "42")
    pub = await repo.complete_publication(item, token, "42", "123")
    assert pub > 0
    assert stored(path, "SELECT status FROM items WHERE id=?", (item,)) == [
        ("published",)
    ]
    assert stored(path, "SELECT state,message_id FROM publication_claims") == [
        ("delivered", "123")
    ]
    assert stored(
        path, "SELECT message FROM logs WHERE message='publication_sent'"
    ) == [("publication_sent",)]
    with pytest.raises(ValueError):
        await repo.complete_publication(item, token, "42", "123")
    assert len(stored(path, "SELECT * FROM publications")) == 1


@pytest.mark.asyncio
async def test_completion_failure_rolls_back_all_bookkeeping_but_keeps_claim(database):
    path, item = database
    repo = AsyncNewsRepository(path)
    token = await repo.claim_publication(item, "42")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER fail_ack BEFORE INSERT ON logs WHEN NEW.message='publication_sent' BEGIN SELECT RAISE(ABORT,'fixture_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await repo.complete_publication(item, token, "42", "123")
    assert not stored(path, "SELECT * FROM publications")
    assert stored(path, "SELECT status FROM items") == [("approved",)]
    assert stored(path, "SELECT state FROM publication_claims") == [("sending",)]
    assert await AsyncNewsRepository(path).claim_publication(item, "42") is None


@pytest.mark.asyncio
async def test_wrong_token_cannot_release_or_complete(database):
    path, item = database
    repo = AsyncNewsRepository(path)
    await repo.claim_publication(item, "42")
    await repo.release_publication_rejection(item, "wrong")
    with pytest.raises(ValueError):
        await repo.complete_publication(item, "wrong", "42", "123")
    assert len(stored(path, "SELECT * FROM publication_claims")) == 1


@pytest.mark.asyncio
async def test_scheduler_and_manual_service_send_once(database, monkeypatch):
    path, item = database
    entered, resume = asyncio.Event(), asyncio.Event()
    bot = MagicMock()

    async def send(**kwargs):
        entered.set()
        await resume.wait()
        return MagicMock(message_id=123)

    bot.send_message = AsyncMock(side_effect=send)
    a, b = service(path, bot, monkeypatch), service(path, bot, monkeypatch)
    first = asyncio.create_task(a.publish_pending())
    await asyncio.wait_for(entered.wait(), 5)
    assert await b.publish_pending() == 0
    resume.set()
    assert await first == 1
    assert await b.publish_pending() == 0
    assert bot.send_message.await_count == 1
    assert len(stored(path, "SELECT * FROM publications")) == 1


@pytest.mark.asyncio
async def test_timeout_never_retries_falls_back_or_releases(database, monkeypatch):
    path, item = database
    repo = AsyncNewsRepository(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE items SET images='https://cdn.example.test/cover.jpg' WHERE id=?",
            (item,),
        )
    bot = MagicMock()
    bot.send_photo = AsyncMock(side_effect=TimeoutError("ACK lost"))
    bot.send_message = AsyncMock()
    svc = service(path, bot, monkeypatch)
    svc._immediate_attempts = 4
    assert await svc.publish_pending() == 0
    await repo.update_status(
        item, "approved"
    )  # even a manual reapproval cannot reset uncertainty
    assert await service(path, bot, monkeypatch).publish_pending() == 0
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    assert stored(path, "SELECT state FROM publication_claims") == [("unknown",)]


@pytest.mark.asyncio
async def test_cancelled_process_path_retains_durable_claim(database, monkeypatch):
    path, item = database
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    bot = MagicMock(send_message=AsyncMock(side_effect=send))
    task = asyncio.create_task(service(path, bot, monkeypatch).publish_pending())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await AsyncNewsRepository(path).claim_publication(item, "42") is None
    assert stored(path, "SELECT state FROM publication_claims") == [("sending",)]


@pytest.mark.asyncio
async def test_partial_media_delivery_blocks_retry_even_if_text_rejected(
    database, monkeypatch
):
    path, item = database
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE items SET images='https://cdn.example.test/cover.jpg' WHERE id=?",
            (item,),
        )
    reject = TelegramBadRequest(
        method=SendMessage(chat_id=42, text="test"), message="bad text"
    )
    bot = MagicMock(
        send_photo=AsyncMock(return_value=MagicMock(message_id=9)),
        send_message=AsyncMock(side_effect=reject),
    )
    svc = service(path, bot, monkeypatch)
    monkeypatch.setattr(svc, "_build_caption", lambda *args: "x" * 2000)
    assert await svc.publish_pending() == 0
    assert stored(path, "SELECT state FROM publication_claims") == [("unknown",)]
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_rejection_allows_retry_but_does_not_leave_a_receipt(
    database, monkeypatch
):
    path, item = database
    reject = TelegramBadRequest(
        method=SendMessage(chat_id=42, text="test"), message="bad text"
    )
    bot = MagicMock(send_message=AsyncMock(side_effect=reject))
    monkeypatch.setattr("services.publication.sync_publication_result", AsyncMock())
    assert await service(path, bot, monkeypatch).publish_pending() == 0
    assert not stored(path, "SELECT * FROM publication_claims")
    assert not stored(path, "SELECT * FROM publications")
    assert stored(path, "SELECT status FROM items") == [("publish_retry",)]


@pytest.mark.asyncio
async def test_sheets_failure_cannot_reclassify_committed_delivery(
    database, monkeypatch
):
    path, item = database
    bot = MagicMock(send_message=AsyncMock(return_value=MagicMock(message_id=123)))
    svc = service(path, bot, monkeypatch)
    svc._handle_success = AsyncMock(side_effect=RuntimeError("Sheets unavailable"))
    assert await svc.publish_pending() == 1
    assert stored(path, "SELECT status FROM items") == [("published",)]
    assert await service(path, bot, monkeypatch).publish_pending() == 0
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_is_the_only_automatic_transport_retry(monkeypatch):
    limit = TelegramRetryAfter(
        method=SendMessage(chat_id=42, text="test"), message="rate limit", retry_after=2
    )
    send = AsyncMock(side_effect=[limit, "ok"])
    sleep = AsyncMock()
    monkeypatch.setattr("services.publication.asyncio.sleep", sleep)
    assert await _send_with_retry(send, attempts=3, delay_seconds=0) == ("ok", 2)
    sleep.assert_awaited_once_with(2.0)
    send = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(PublicationSendError):
        await _send_with_retry(send, attempts=3, delay_seconds=0)
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_bookkeeping_after_ack_cannot_send_again(database, monkeypatch):
    path, item = database
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER fail_ack BEFORE INSERT ON logs WHEN NEW.message='publication_sent' BEGIN SELECT RAISE(ABORT,'fixture_failure'); END"
        )
    bot = MagicMock(send_message=AsyncMock(return_value=MagicMock(message_id=123)))
    with pytest.raises(sqlite3.IntegrityError):
        await service(path, bot, monkeypatch).publish_pending()
    assert await service(path, bot, monkeypatch).publish_pending() == 0
    assert not stored(path, "SELECT * FROM publications")
    assert stored(path, "SELECT state FROM publication_claims") == [("sending",)]
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_migration_is_additive_and_idempotent(database):
    path, item = database
    await initialize_database(path)
    assert stored(path, "SELECT id FROM items") == [(item,)]
    assert stored(
        path,
        "SELECT count(*) FROM schema_migrations WHERE version='005_publication_claims'",
    ) == [(1,)]


def readiness_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts/check_publication_guard_readiness.py"
    )
    spec = importlib.util.spec_from_file_location("guard_readiness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_cleanup_preserves_claims_and_blocked_items_do_not_starve_queue(database):
    path, item = database
    repo = AsyncNewsRepository(path)
    await repo.claim_publication(item, "42")
    other = await repo.create_item(
        {"source": "test", "content": "Other", "status": "approved"}
    )
    assert [row["id"] for row in await repo.list_publication_candidates(limit=1)] == [
        other
    ]
    assert await repo.count_publication_queue() == 1
    assert (
        await repo.delete_items_before(datetime.now(timezone.utc) + timedelta(days=1))
        == 1
    )
    assert stored(path, "SELECT id FROM items") == [(item,)]
    assert stored(path, "SELECT state FROM publication_claims") == [("sending",)]


@pytest.mark.asyncio
async def test_readiness_does_not_write_database_or_expose_content(database):
    path, item = database
    before = path.read_bytes()
    result = readiness_module().inspect(path)
    assert result["queued_status_counts"] == {"approved": 1}
    assert result["claims_table_present"] is True
    assert result["migration_recorded"] is True
    assert result["deploy_ready"] is False
    assert (
        result["database_writes"]
        == result["external_requests"]
        == result["telegram_sends"]
        == 0
    )
    assert "Owner note" not in json.dumps(result)
    assert path.read_bytes() == before


def test_readiness_observes_legacy_database_without_applying_migration(tmp_path):
    path = tmp_path / "legacy.db"
    schema = Path(__file__).resolve().parents[1] / "storage/migrations/001_init.sql"
    with sqlite3.connect(path) as db:
        db.executescript(schema.read_text(encoding="utf-8"))
    before = path.read_bytes()
    result = readiness_module().inspect(path)
    assert result["claim_state_counts"] is None
    assert result["migration_recorded"] is False
    assert path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        readiness_module().inspect(missing)
    assert not missing.exists()
