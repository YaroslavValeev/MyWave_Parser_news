import asyncio
import os
import tempfile
import pytest
from datetime import datetime, timezone
from utils.row_utils import generate_checksum

from storage.repository import initialize_database, AsyncNewsRepository, DuplicateItemError


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_initialize_and_crud(tmp_path):
    db_file = tmp_path / "test_db.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)

    item = {
        "source": "unittest",
        "title": "Test title",
        "content": "Some content",
    }

    item_id = await repo.create_item(item)
    assert isinstance(item_id, int)

    fetched = await repo.get_item(item_id)
    assert fetched is not None
    assert fetched["title"] == item["title"]

    # duplicate checksum should raise DuplicateItemError
    with pytest.raises(DuplicateItemError):
        await repo.create_item(item)

    # nlp results
    await repo.save_nlp_results(item_id, summary="sum", questions=["q1"], decision="review")
    nlp = await repo.get_nlp_results(item_id)
    assert nlp and nlp["summary"] == "sum"

    # publication
    pub_id = await repo.save_publication(item_id, "chan", "msg")
    assert isinstance(pub_id, int)
    pub = await repo.get_publication_by_item(item_id)
    assert pub and pub["message_id"] == "msg"

    # logs
    log_id = await repo.log_event(item_id, "info", "test", {"k": 1})
    assert isinstance(log_id, int)
    last = await repo.get_last_log(item_id, "test")
    assert last and last["message"] == "test"

    # cleanup
    count = await repo.delete_items_before(datetime.now(timezone.utc))
    assert isinstance(count, int)


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_list_review_queue_priority(tmp_path):
    """Сначала review, затем new при одинаковой очереди."""
    db_file = tmp_path / "q.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)

    id_new = await repo.create_item(
        {
            "source": "t",
            "title": "Older new",
            "content": "a",
            "link": "https://a.example",
            "status": "new",
        }
    )
    id_review = await repo.create_item(
        {
            "source": "t",
            "title": "Review item",
            "content": "b",
            "link": "https://b.example",
            "status": "review",
        }
    )
    assert id_new != id_review

    q = await repo.list_review_queue(limit=5)
    assert [r["id"] for r in q] == [id_review, id_new]


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_upsert_author_notes(tmp_path):
    db_file = tmp_path / "notes.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {"source": "t", "title": "x", "content": "y", "link": "https://x.example"}
    )
    await repo.save_nlp_results(item_id, summary="keep me", decision="review")
    await repo.upsert_author_notes(item_id, "  Owner says hi  ")
    nlp = await repo.get_nlp_results(item_id)
    assert nlp and nlp["summary"] == "keep me"
    assert nlp.get("author_notes") == "Owner says hi"


@pytest.mark.asyncio
async def test_requeue_error_to_new(tmp_path):
    db_file = tmp_path / "requeue.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    e1 = await repo.create_item(
        {"source": "t", "title": "a", "content": "c", "link": "https://a.example", "status": "error"}
    )
    e2 = await repo.create_item(
        {"source": "t", "title": "b", "content": "c", "link": "https://b.example", "status": "error"}
    )
    await repo.create_item(
        {"source": "t", "title": "c", "content": "c", "link": "https://c.example", "status": "review"}
    )
    n = await repo.requeue_error_to_new(limit=1)
    assert n == 1
    i1 = await repo.get_item(e1)
    i2 = await repo.get_item(e2)
    assert i1["status"] == "new"
    assert i2["status"] == "error"
    n2 = await repo.requeue_error_to_new(limit=10)
    assert n2 == 1
    assert (await repo.get_item(e2))["status"] == "new"


@pytest.mark.asyncio
async def test_get_status_counts(tmp_path):
    db_file = tmp_path / "counts.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    await repo.create_item(
        {"source": "t", "title": "n", "content": "x", "link": "https://n.example", "status": "new"}
    )
    await repo.create_item(
        {"source": "t", "title": "e", "content": "x", "link": "https://e.example", "status": "error"}
    )
    counts = await repo.get_status_counts()
    assert counts.get("new") == 1
    assert counts.get("error") == 1


@pytest.mark.asyncio
async def test_item_exists_by_checksum_and_content(tmp_path):
    db_file = tmp_path / "dup.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    ch = generate_checksum({"raw_title": "dup", "raw_content": "c", "raw_html": ""})
    await repo.create_item(
        {
            "source": "rss:src",
            "title": "dup",
            "content": "c",
            "link": "https://dup.example",
            "checksum": ch,
        }
    )
    assert await repo.item_exists_by_checksum(ch)
    assert await repo.item_exists_by_content("dup", "c", "")
    assert not await repo.item_exists_by_checksum("")
    assert not await repo.item_exists_by_checksum("   ")


@pytest.mark.asyncio
async def test_create_item_accepts_raw_aliases(tmp_path):
    db_file = tmp_path / "raw.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    iid = await repo.create_item(
        {
            "source_type": "youtube",
            "source_name": "Ch",
            "source_url": "https://watch?v=1",
            "raw_title": "Vid",
            "raw_content": "desc",
            "status": "new",
        }
    )
    row = await repo.get_item(iid)
    assert row["title"] == "Vid"
    assert row["content"] == "desc"
    assert row["link"] == "https://watch?v=1"
    assert row["source"] == "youtube:Ch"


@pytest.mark.asyncio
async def test_update_item_content(tmp_path):
    db_file = tmp_path / "update_content.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    iid = await repo.create_item(
        {
            "source": "rss:test",
            "title": "A",
            "content": "",
            "link": "https://example.com/a",
        }
    )
    await repo.update_item_content(iid, "Новый текст")
    row = await repo.get_item(iid)
    assert row is not None
    assert row["content"] == "Новый текст"


@pytest.mark.asyncio
async def test_list_items_by_status_desc(tmp_path):
    db_file = tmp_path / "ord.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    id1 = await repo.create_item(
        {"source": "t", "title": "a", "content": "x", "link": "https://a.example", "status": "new"}
    )
    id2 = await repo.create_item(
        {"source": "t", "title": "b", "content": "x", "link": "https://b.example", "status": "new"}
    )
    asc = await repo.list_items_by_status("new", limit=10, order="ASC")
    desc = await repo.list_items_by_status("new", limit=10, order="DESC")
    assert [r["id"] for r in asc] == [id1, id2]
    assert [r["id"] for r in desc] == [id2, id1]


@pytest.mark.asyncio
async def test_save_nlp_results_moderation_dict_json(tmp_path):
    """moderation от API — dict; в SQLite пишется JSON-строка (не sqlite.ProgrammingError)."""
    db_file = tmp_path / "mod.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {"source": "t", "title": "x", "content": "y", "link": "https://m.example"}
    )
    await repo.save_nlp_results(
        item_id,
        summary="s",
        questions=["q1", "q2"],
        decision="review",
        moderation={"flagged": False, "categories": {"x": 0.1}},
        extra={"k": 1},
    )
    row = await repo.get_nlp_results(item_id)
    assert row is not None
    assert row["summary"] == "s"
    assert row["questions"] == ["q1", "q2"]
    assert isinstance(row.get("moderation"), dict)
    assert row["moderation"].get("flagged") is False


@pytest.mark.asyncio
async def test_save_nlp_results_persists_merged_text(tmp_path):
    db_file = tmp_path / "merged.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {"source": "t", "title": "x", "content": "y", "link": "https://merged.example"}
    )

    await repo.save_nlp_results(
        item_id,
        summary="Черновое саммари",
        decision="review",
        merged_text="Финальная версия",
    )

    row = await repo.get_nlp_results(item_id)
    assert row is not None
    assert row["summary"] == "Черновое саммари"
    assert row["merged_text"] == "Финальная версия"


@pytest.mark.asyncio
async def test_list_items_latest_first(tmp_path):
    db_file = tmp_path / "latest.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    id1 = await repo.create_item(
        {"source": "t", "title": "old", "content": "x", "link": "https://o.example"}
    )
    id2 = await repo.create_item(
        {"source": "t", "title": "new", "content": "x", "link": "https://n.example"}
    )
    latest = await repo.list_items(limit=2)
    assert [r["id"] for r in latest] == [id2, id1]


@pytest.mark.asyncio
async def test_publication_candidates_include_approved_and_explicit_publication_queue(tmp_path):
    db_file = tmp_path / "pubq.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    approved_id = await repo.create_item(
        {"source": "t", "title": "approved", "content": "x", "link": "https://a.example", "status": "approved"}
    )
    retry_id = await repo.create_item(
        {"source": "t", "title": "retry", "content": "x", "link": "https://r.example", "status": "publish_retry"}
    )
    await repo.create_item(
        {"source": "t", "title": "error", "content": "x", "link": "https://e.example", "status": "error"}
    )

    queue = await repo.list_publication_candidates(limit=10)
    queue_ids = {row["id"] for row in queue}
    assert approved_id in queue_ids
    assert retry_id in queue_ids
    assert await repo.count_publication_queue() == 2
