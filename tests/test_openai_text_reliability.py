import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI, AuthenticationError, RateLimitError

from nlp.openai_client import OpenAIClient, OpenAISettings


def settings(**kwargs):
    return OpenAISettings(
        "test-key", "gpt-4o-mini", "whisper-1", "gpt-image-1", "ru", **kwargs
    )


def completion(content="Русское резюме", finish="stop", refusal=None):
    return {
        "id": "test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 10},
        },
    }


@pytest.mark.asyncio
async def test_real_sdk_4o_mini_request_and_safe_metrics(caplog):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=completion())

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with caplog.at_level(logging.INFO, logger="nlp.openai_client"):
            assert (
                await OpenAIClient(settings(), client=sdk)._chat_completion(
                    "system private", "source private"
                )
                == "Русское резюме"
            )
    payload = requests[0]
    assert (
        payload["model"] == "gpt-4o-mini" and payload["max_completion_tokens"] == 1600
    )
    assert payload["temperature"] == 0.2 and "reasoning_effort" not in payload
    assert payload["messages"][1]["content"] == "source private"
    assert "input_tokens=100" in caplog.text and "output_tokens=25" in caplog.text
    assert "cached_tokens=10" in caplog.text and "latency_ms=" in caplog.text
    assert "source private" not in caplog.text and "test-key" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        completion(finish="length"),
        completion(refusal="refused"),
        completion(content=""),
        completion(content=None),
        {**completion(), "choices": []},
    ],
)
async def test_partial_or_refused_text_is_not_valid_content(body):
    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as sdk:
        with pytest.raises(RuntimeError):
            await OpenAIClient(settings(), client=sdk)._chat_completion("s", "u")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_type,expected_calls",
    [(429, RateLimitError, 2), (401, AuthenticationError, 1)],
)
async def test_api_failure_has_bounded_retries_and_no_text_model_switch(
    status, error_type, expected_calls, caplog
):
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        return httpx.Response(
            status,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "private response body", "type": "api_error"}},
        )

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with pytest.raises(error_type):
            await OpenAIClient(settings(), client=sdk)._chat_completion(
                "s", "private source"
            )
    assert models == ["gpt-4o-mini"] * expected_calls
    assert (
        "private response body" not in caplog.text
        and "private source" not in caplog.text
    )


@pytest.mark.asyncio
async def test_total_deadline_stops_sdk_retry_wait():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, headers={"retry-after": "1"}, json={"error": {"message": "rate limit"}}
        )

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with pytest.raises(TimeoutError):
            await OpenAIClient(
                settings(text_timeout_seconds=0.02), client=sdk
            )._chat_completion("s", "u")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text_max_completion_tokens": 0},
        {"text_timeout_seconds": 0},
        {"text_timeout_seconds": float("nan")},
        {"text_max_retries": 3},
    ],
)
def test_invalid_limits_fail_before_api(kwargs):
    with pytest.raises(ValueError):
        settings(**kwargs)


@pytest.mark.asyncio
async def test_one_translation_retry_succeeds_without_generic_fallback():
    from unittest.mock import AsyncMock

    client = OpenAIClient(settings())
    client._chat_completion = AsyncMock(
        side_effect=[
            "PCM engine winter storage instructions.",
            "Инструкция по зимнему хранению двигателя PCM.",
        ]
    )
    assert (
        await client.summarize("PCM engine winter storage instructions.")
        == "Инструкция по зимнему хранению двигателя PCM."
    )
    assert client._chat_completion.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,model",
    [("permission_denied", "other-audio"), ("model_not_found", "whisper-1")],
)
async def test_transcription_does_not_retry_permissions_or_same_model(
    tmp_path, code, model
):
    from unittest.mock import AsyncMock, MagicMock

    exc = RuntimeError("private body")
    exc.body = {"error": {"code": code}}
    api = MagicMock()
    api.audio.transcriptions.create = AsyncMock(side_effect=exc)
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"audio")
    opts = settings()
    opts.whisper_model = model
    with pytest.raises(RuntimeError):
        await OpenAIClient(opts, client=api).transcribe_audio(audio)
    assert api.audio.transcriptions.create.await_count == 1


@pytest.mark.asyncio
async def test_failed_translation_stays_review_without_generated_facts(tmp_path):
    from unittest.mock import AsyncMock
    from services.nlp_pipeline import reprocess_items
    from storage.repository import AsyncNewsRepository, initialize_database

    db = tmp_path / "data.db"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    item_id = await repo.create_item(
        {
            "source": "test",
            "link": "https://news.example.test/translation",
            "content": "PCM engine winter storage instructions.",
        }
    )
    client = OpenAIClient(settings())
    client._chat_completion = AsyncMock(
        return_value="PCM engine winter storage instructions."
    )
    assert await reprocess_items([item_id], repository=repo, client=client) == 0
    assert (await repo.get_item(item_id))["status"] == "review"
    assert await repo.get_nlp_results(item_id) is None
    assert client._chat_completion.await_count == 2


@pytest.mark.asyncio
async def test_all_text_roles_keep_model_source_notes_and_question_lines():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json=completion("- Вопрос один?\n- Вопрос два?\n- Вопрос три?")
        )

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIClient(settings(), client=sdk)
        await client.summarize("Исходная статья о PCM")
        assert len(await client.gen_questions("Исходная статья о PCM")) == 3
        await client.author_rewrite(
            "Исходная статья о PCM", "Мой комментарий", base_summary="Саммари"
        )
    assert len(requests) == 3 and all(r["model"] == "gpt-4o-mini" for r in requests)
    assert all("Исходная статья о PCM" in r["messages"][1]["content"] for r in requests)
    assert "Мой комментарий" in requests[-1]["messages"][1]["content"]
