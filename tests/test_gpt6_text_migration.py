import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI, RateLimitError

from config.settings import config
from nlp.openai_client import OpenAIClient, OpenAISettings


def settings(model="gpt-6-luna", effort="none", **kwargs):
    return OpenAISettings(
        "test-key",
        model,
        "whisper-1",
        "gpt-image-1",
        "ru",
        text_reasoning_effort=effort,
        **kwargs,
    )


def completion(content="Русское резюме", finish="stop", refusal=None):
    return {
        "id": "test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-6-luna",
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
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,effort,temperature",
    [
        ("gpt-6-luna", "none", True),
        ("gpt-6-luna", "low", False),
        ("gpt-6-luna", "high", False),
        ("gpt-4o-mini", "none", True),
    ],
)
async def test_real_sdk_serializes_compatible_request(
    model, effort, temperature, caplog
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=completion())

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIClient(settings(model, effort), client=sdk)
        with caplog.at_level(logging.INFO, logger="nlp.openai_client"):
            assert (
                await client._chat_completion("system private", "source private")
                == "Русское резюме"
            )
    payload = requests[0]
    assert payload["model"] == model
    assert payload["max_completion_tokens"] == 1600
    assert ("temperature" in payload) is temperature
    if model == "gpt-6-luna":
        assert payload["reasoning_effort"] == effort
    else:
        assert "reasoning_effort" not in payload
    assert payload["messages"][1]["content"] == "source private"
    record = next(r for r in caplog.records if r.msg == "text_completion_succeeded")
    assert record.input_tokens == 100 and record.output_tokens == 25
    assert record.reasoning_tokens == 0 and record.cached_tokens == 10
    assert record.latency_ms >= 0
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
async def test_partial_or_refused_text_is_not_returned_as_valid_content(body):
    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as sdk:
        with pytest.raises(RuntimeError):
            await OpenAIClient(settings(), client=sdk)._chat_completion("s", "u")


@pytest.mark.asyncio
async def test_all_text_roles_use_selected_model_and_keep_source_and_notes():
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
        await client.summarize("Реальный текст о PCM")
        assert len(await client.gen_questions("Реальный текст о PCM")) == 3
        await client.author_rewrite(
            "Реальный текст о PCM", "Мой комментарий", base_summary="Саммари"
        )
    assert len(requests) == 3
    assert all(
        r["model"] == "gpt-6-luna" and r["reasoning_effort"] == "none" for r in requests
    )
    assert all("Реальный текст о PCM" in r["messages"][1]["content"] for r in requests)
    assert "Мой комментарий" in requests[-1]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_quota_error_has_bounded_retries_and_no_model_fallback():
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        return httpx.Response(
            429,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "rate limit", "type": "rate_limit"}},
        )

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with pytest.raises(RateLimitError):
            await OpenAIClient(settings(), client=sdk)._chat_completion("s", "u")
    assert models == ["gpt-6-luna", "gpt-6-luna"]


@pytest.mark.asyncio
async def test_total_text_deadline_includes_sdk_retries():
    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=completion())

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with pytest.raises(TimeoutError):
            await OpenAIClient(
                settings(text_timeout_seconds=0.01), client=sdk
            )._chat_completion("s", "u")


@pytest.mark.parametrize(
    "model,effort,kwargs",
    [
        ("gpt-6.1-sol", "none", {}),
        ("gpt-6-luna", "minimal", {}),
        ("gpt-6-luna", "none", {"text_max_completion_tokens": 0}),
        ("gpt-6-luna", "none", {"text_timeout_seconds": 0}),
        ("gpt-6-luna", "none", {"text_max_retries": 3}),
    ],
)
def test_invalid_migration_configuration_fails_before_api(model, effort, kwargs):
    with pytest.raises(ValueError):
        settings(model, effort, **kwargs)


def test_model_selection_remains_explicit_and_settings_flow_from_config(monkeypatch):
    monkeypatch.setattr(config, "TEXT_MODEL", "gpt-4o-mini")
    assert OpenAISettings.from_config().text_model == "gpt-4o-mini"
    monkeypatch.setattr(config, "TEXT_MODEL", "gpt-6-luna")
    monkeypatch.setattr(config, "TEXT_REASONING_EFFORT", "low")
    assert OpenAISettings.from_config().text_reasoning_effort == "low"
