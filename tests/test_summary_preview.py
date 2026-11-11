import hashlib
import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/preview_article_summary.py"
SPEC = importlib.util.spec_from_file_location("summary_preview", SCRIPT)
PREVIEW = importlib.util.module_from_spec(SPEC)
disabled = __import__("logging").root.manager.disable
SPEC.loader.exec_module(PREVIEW)
__import__("logging").disable(disabled)
URL = "https://wakeflot.ru/news/1786"
TEXT = "Подготовка двигателя Malibu M5 M6 к зимнему хранению. " * 10


def database(path, status="review", content=None):
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY,status TEXT,content TEXT,transcript TEXT,link TEXT,images TEXT);"
            "CREATE TABLE nlp_results(item_id INTEGER,summary TEXT,author_notes TEXT);"
            "CREATE TABLE publications(item_id INTEGER);"
        )
        db.execute(
            "INSERT INTO items VALUES(626,?,?,?,?,?)",
            (
                status,
                content or f"Подробнее: [{URL}]({URL})",
                "",
                "https://t.me/wakeflot/3043",
                "cover.jpg",
            ),
        )
        db.execute(
            "INSERT INTO nlp_results VALUES(626,?,?)",
            ("Старое саммари", "Мой комментарий"),
        )


def evidence(item):
    return {
        "version": 1,
        "requested_url": URL,
        "final_url": URL,
        "title": "Зимнее хранение Malibu M5 M6",
        "text": TEXT,
        "input_sha256": PREVIEW.source_input_hash(item),
        "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
    }


def client():
    sdk = SimpleNamespace(close=AsyncMock())
    return SimpleNamespace(
        _ensure_client=AsyncMock(return_value=sdk),
        summarize=AsyncMock(
            return_value="Двигатели Malibu M5/M6 готовят к зимнему хранению."
        ),
    ), sdk


@pytest.mark.asyncio
async def test_preview_summarizes_exact_article_and_preserves_all_database_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "data.db"
    database(path)
    before = path.read_bytes()
    ai, sdk = client()
    retriever = AsyncMock(side_effect=lambda item, hosts: evidence(item))
    monkeypatch.setattr(PREVIEW.config, "TEXT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(
        PREVIEW.config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("wakeflot.ru",)
    )
    result = await PREVIEW.preview(path, 626, URL, client=ai, retriever=retriever)
    ai.summarize.assert_awaited_once_with(TEXT, lang="ru", max_words=120)
    sdk.close.assert_awaited_once()
    assert result["database_writes"] == result["telegram_sends"] == 0
    assert result["status_preserved"] == "review" and path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["discarded", "published", "approved", "processing"])
async def test_ineligible_item_is_rejected_before_any_fetch_or_api(
    tmp_path, monkeypatch, status
):
    path = tmp_path / "data.db"
    database(path, status=status)
    before = path.read_bytes()
    ai, sdk = client()
    retriever = AsyncMock()
    with pytest.raises(PREVIEW.PreviewRefused, match="item_not_eligible"):
        await PREVIEW.preview(path, 626, URL, client=ai, retriever=retriever)
    retriever.assert_not_awaited()
    ai._ensure_client.assert_not_awaited()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_preview_refuses_changed_source_and_still_closes_client(
    tmp_path, monkeypatch
):
    path = tmp_path / "data.db"
    database(path)
    ai, sdk = client()
    monkeypatch.setattr(PREVIEW.config, "TEXT_MODEL", "gpt-4o-mini")

    async def summarize(*args, **kwargs):
        with sqlite3.connect(path) as db:
            db.execute("UPDATE items SET status=? WHERE id=626", ("discarded",))
        return "Саммари"

    ai.summarize.side_effect = summarize
    with pytest.raises(PREVIEW.PreviewRefused, match="source_or_status_changed"):
        await PREVIEW.preview(
            path,
            626,
            URL,
            client=ai,
            retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
        )
    sdk.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_protects_649_and_refuses_wrong_url_before_api(
    tmp_path, monkeypatch
):
    path = tmp_path / "data.db"
    database(path)
    ai, sdk = client()
    monkeypatch.setattr(PREVIEW.config, "TEXT_MODEL", "gpt-4o-mini")
    for item_id, expected, reason in [
        (649, URL, "protected_item"),
        (626, "https://wakeflot.ru/news/1785", "unexpected_source"),
    ]:
        with pytest.raises(PREVIEW.PreviewRefused, match=reason):
            await PREVIEW.preview(
                path, item_id, expected, client=ai, retriever=AsyncMock()
            )
    ai._ensure_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_failure_closes_client_and_keeps_database_unchanged(
    tmp_path, monkeypatch
):
    path = tmp_path / "data.db"
    database(path)
    before = path.read_bytes()
    monkeypatch.setattr(PREVIEW.config, "TEXT_MODEL", "gpt-4o-mini")
    ai, sdk = client()
    ai.summarize.side_effect = RuntimeError("private API body")
    with pytest.raises(RuntimeError):
        await PREVIEW.preview(
            path,
            626,
            URL,
            client=ai,
            retriever=AsyncMock(side_effect=lambda item, hosts: evidence(item)),
        )
    sdk.close.assert_awaited_once()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_unbound_article_is_refused_before_api(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    database(path)
    monkeypatch.setattr(PREVIEW.config, "TEXT_MODEL", "gpt-4o-mini")
    ai, sdk = client()

    def wrong(item, hosts):
        return {**evidence(item), "text_sha256": "tampered"}

    with pytest.raises(PREVIEW.PreviewRefused, match="invalid_source_evidence"):
        await PREVIEW.preview(
            path, 626, URL, client=ai, retriever=AsyncMock(side_effect=wrong)
        )
    ai._ensure_client.assert_not_awaited()
