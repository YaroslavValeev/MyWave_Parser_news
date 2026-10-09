import pytest

from nlp.openai_client import OpenAIClient, OpenAISettings


def _client() -> OpenAIClient:
    return OpenAIClient(
        settings=OpenAISettings(
            api_key="test",
            text_model="gpt-test",
            whisper_model="whisper-test",
            image_model="image-test",
            default_language="ru",
        )
    )


@pytest.mark.asyncio
async def test_translate_text_prompts_for_russian():
    client = _client()
    captured: dict[str, str] = {}

    async def fake_chat(system_prompt: str, user_content: str) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_content
        return "Русский перевод"

    client._chat_completion = fake_chat  # type: ignore[method-assign]

    result = await client.translate_text("Wake cable olympic entry", lang="ru")

    assert result == "Русский перевод"
    assert "русский" in captured["system"].lower()
    assert "Wake cable olympic entry" in captured["user"]
