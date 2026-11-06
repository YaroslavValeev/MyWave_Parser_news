from __future__ import annotations

import pytest

from services.nlp_pipeline import process_nlp_queue
from storage.repository import AsyncNewsRepository, initialize_database


class AuthFailingClient:
    async def summarize(self, *args, **kwargs):
        raise Exception("Error code: 401 - Incorrect API key provided")


class TimeoutFailingClient:
    async def summarize(self, *args, **kwargs):
        raise Exception("Request timed out.")


class QuotaFailingClient:
    async def summarize(self, *args, **kwargs):
        raise Exception("Error code: 429 - insufficient_quota: You exceeded your current quota")


@pytest.mark.asyncio
async def test_process_nlp_falls_back_to_review_on_nonrecoverable_openai_error(tmp_path):
    db_file = tmp_path / "nlp_fallback.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "Fallback title",
            "content": "Fallback content for manual review flow.",
            "link": "https://example.com/fallback",
            "status": "new",
        }
    )

    processed = await process_nlp_queue(repository=repo, client=AuthFailingClient(), batch_size=1)

    assert processed == 1
    item = await repo.get_item(item_id)
    assert item["status"] == "review"
    nlp = await repo.get_nlp_results(item_id)
    assert "Fallback content" not in nlp["summary"]
    assert "Англоязычный материал" in nlp["summary"]
    assert await repo.get_last_log(item_id, "nlp_fallback_review")


@pytest.mark.asyncio
async def test_process_nlp_falls_back_to_review_on_insufficient_quota(tmp_path):
    db_file = tmp_path / "nlp_quota.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "Quota title",
            "content": "Quota content for manual review flow.",
            "link": "https://example.com/quota",
            "status": "new",
        }
    )

    processed = await process_nlp_queue(repository=repo, client=QuotaFailingClient(), batch_size=1)

    assert processed == 1
    item = await repo.get_item(item_id)
    assert item["status"] == "review"
    assert await repo.get_last_log(item_id, "nlp_fallback_review")


@pytest.mark.asyncio
async def test_process_nlp_keeps_transient_errors_in_error_status(tmp_path):
    db_file = tmp_path / "nlp_timeout.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "Timeout title",
            "content": "Timeout content",
            "link": "https://example.com/timeout",
            "status": "new",
        }
    )

    processed = await process_nlp_queue(repository=repo, client=TimeoutFailingClient(), batch_size=1)

    assert processed == 0
    item = await repo.get_item(item_id)
    assert item["status"] == "error"
    assert await repo.get_last_log(item_id, "nlp_processing_failed")


@pytest.mark.asyncio
async def test_process_nlp_skips_title_only_context_and_sends_to_review(tmp_path):
    db_file = tmp_path / "nlp_missing_context.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "ДИАЛОГИ О РЫБАЛКЕ",
            "title": "Cristina Kolesnikova",
            "content": "",
            "link": "https://t.me/talktofish/347",
            "status": "new",
        }
    )

    processed = await process_nlp_queue(repository=repo, client=QuotaFailingClient(), batch_size=1)

    assert processed == 1
    item = await repo.get_item(item_id)
    assert item["status"] == "review"
    nlp = await repo.get_nlp_results(item_id)
    assert "нет текстового контента" in nlp["summary"]
    assert await repo.get_last_log(item_id, "nlp_skipped_missing_text_context")
