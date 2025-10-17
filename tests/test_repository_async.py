import asyncio
import os
import tempfile
import pytest
from datetime import datetime, timezone
from storage.repository import initialize_database, AsyncNewsRepository, DuplicateItemError


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
