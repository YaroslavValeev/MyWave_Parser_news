"""OPENAI_SKIP_MODERATION должен обходить Moderations API."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nlp.openai_client import OpenAIClient, OpenAISettings


@pytest.mark.asyncio
async def test_moderate_skipped_when_flag_enabled():
    settings = OpenAISettings(
        api_key="sk-test",
        text_model="gpt-4o-mini",
        whisper_model="whisper-1",
        image_model="gpt-image-1",
        default_language="ru",
    )
    client = OpenAIClient(settings=settings, client=MagicMock())
    with patch("nlp.openai_client.config") as cfg:
        cfg.OPENAI_SKIP_MODERATION = True
        result = await client.moderate("any text")
    assert result["flagged"] is False
    assert result.get("skipped") is True


@pytest.mark.asyncio
async def test_moderate_calls_api_when_flag_disabled():
    api = MagicMock()
    api.moderations.create = AsyncMock(
        return_value=MagicMock(results=[MagicMock(model_dump=lambda: {"flagged": False})])
    )
    settings = OpenAISettings(
        api_key="sk-test",
        text_model="gpt-4o-mini",
        whisper_model="whisper-1",
        image_model="gpt-image-1",
        default_language="ru",
    )
    client = OpenAIClient(settings=settings, client=api)
    with patch("nlp.openai_client.config") as cfg:
        cfg.OPENAI_SKIP_MODERATION = False
        result = await client.moderate("ok")
    api.moderations.create.assert_awaited_once()
    assert result["flagged"] is False
