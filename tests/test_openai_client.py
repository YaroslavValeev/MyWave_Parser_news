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
async def test_summarize_prompts_for_strict_russian_translation():
    client = _client()
    captured: dict[str, str] = {}

    async def fake_chat(system_prompt: str, user_content: str) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_content
        return "Русское саммари"

    client._chat_completion = fake_chat  # type: ignore[method-assign]

    result = await client.summarize("Wakeboarding Magazine covers the latest news.", lang="ru")

    assert result == "Русское саммари"
    assert "строго на русском языке" in captured["system"]
    assert "переведи" in captured["system"]
    assert "Wakeboarding Magazine covers" in captured["user"]


@pytest.mark.asyncio
async def test_summarize_forces_russian_fallback_for_english_response():
    client = _client()

    async def fake_chat(system_prompt: str, user_content: str) -> str:
        return "Wakeboarding Magazine covers the latest industry news and rider interviews."

    client._chat_completion = fake_chat  # type: ignore[method-assign]

    result = await client.summarize("Wakeboarding Magazine covers the latest news.", lang="ru")

    assert "Wakeboarding Magazine covers" not in result
    assert any(ch in result.lower() for ch in "абвгдежзийклмнопрстуфхцчшщьыэюя")


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_whisper_1_on_model_not_found(tmp_path):
    client = _client()
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake-audio")

    class ModelNotFoundError(Exception):
        status_code = 403

        def __init__(self) -> None:
            super().__init__("model not found")
            self.body = {"error": {"code": "model_not_found"}}

    calls: list[str] = []

    class FakeAudio:
        class transcriptions:
            @staticmethod
            async def create(*, model: str, file, language: str, response_format: str) -> str:
                calls.append(model)
                if model != "whisper-1":
                    raise ModelNotFoundError()
                return "распознанный текст"

    class FakeClient:
        audio = FakeAudio()

    client._client = FakeClient()  # type: ignore[assignment]

    result = await client.transcribe_audio(audio_path, lang="ru")

    assert result == "распознанный текст"
    assert calls == ["whisper-test", "whisper-1"]


@pytest.mark.asyncio
async def test_author_rewrite_uses_original_text_and_notes():
    client = _client()

    captured: dict[str, str] = {}

    async def fake_chat(system_prompt: str, user_content: str) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_content
        return "Готовый текст"

    client._chat_completion = fake_chat  # type: ignore[method-assign]

    result = await client.author_rewrite(
        "Original source text about the event.",
        "Добавь моё мнение про участие в старте.",
        base_summary="Краткое саммари",
        lang="ru",
    )

    assert result == "Готовый текст"
    assert "Original source text about the event." in captured["user"]
    assert "Краткое саммари" in captured["user"]
    assert "Добавь моё мнение про участие в старте." in captured["user"]
    assert "личный пост автора" in captured["system"] or "от лица автора канала" in captured["system"]
    assert "Личная заметка" in captured["system"]
