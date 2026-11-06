"""Логирование NLP при пустой очереди."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from services.nlp_pipeline import process_nlp_queue
from storage.repository import AsyncNewsRepository, initialize_database


@pytest.mark.asyncio
async def test_process_nlp_empty_queue_logs_status_counts(caplog, tmp_path):
    db_file = tmp_path / "nlp_metrics.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    await repo.create_item(
        {
            "source": "t",
            "title": "only_review",
            "content": "x",
            "link": "https://r.example",
            "status": "review",
        }
    )
    caplog.set_level(logging.INFO)
    n = await process_nlp_queue(repository=repo, client=MagicMock())
    assert n == 0
    assert any("NLP: очередь пуста" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_process_nlp_empty_queue_hints_requeue_when_errors(caplog, tmp_path):
    """При new=0 и error>0 — подсказка про requeue (план 10/10, видимость владельцу)."""
    db_file = tmp_path / "nlp_err.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    await repo.create_item(
        {
            "source": "t",
            "title": "bad",
            "content": "x",
            "link": "https://e.example",
            "status": "error",
        }
    )
    caplog.set_level(logging.INFO)
    n = await process_nlp_queue(repository=repo, client=MagicMock())
    assert n == 0
    assert any("requeue" in rec.message.lower() for rec in caplog.records)
