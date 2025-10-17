"""Обёртка над OpenAI SDK для задач NLP проекта."""
from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from config.settings import config
"""Обёртка над OpenAI SDK для задач NLP проекта."""
from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from config.settings import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI
else:
    AsyncOpenAI = Any  # type: ignore[assignment]


@dataclass(slots=True)
class OpenAISettings:
    api_key: str
    text_model: str
    whisper_model: str
    image_model: str
    default_language: str

    @classmethod
    def from_config(cls) -> "OpenAISettings":
        return cls(
            api_key=config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", ""),
            text_model=config.TEXT_MODEL,
            whisper_model=config.WHISPER_MODEL,
            image_model=config.IMAGE_MODEL,
            default_language=config.NL_LANG,
        )


class OpenAIClient:
    """Упрощённый фасад для текстовых, аудио- и визуальных задач."""

    def __init__(
        self,
        settings: OpenAISettings | None = None,
        *,
        client: "AsyncOpenAI" | None = None,
    ) -> None:
        self._settings = settings or OpenAISettings.from_config()
        self._client = client
        self._lock = asyncio.Lock()

    async def summarize(
        self,
        text: str,
        *,
        lang: str | None = None,
        max_words: int = 120,
    ) -> str:
        """Сформировать краткое саммари текста."""

        prompt = (
            "Сделай краткое новостное резюме до {max_words} слов."
            " Используй язык {lang}."
        ).format(max_words=max_words, lang=lang or self._settings.default_language)
        response = await self._chat_completion(
            prompt,
            text,
        )
        return _normalize_text(response)

    async def gen_questions(
        self,
        text: str,
        n: int = 3,
        *,
        lang: str | None = None,
    ) -> list[str]:
        """Сгенерировать уточняющие вопросы по тексту."""

        prompt = (
            "Сформулируй {n} уточняющих вопроса к материалу на языке {lang}."
            " Ответ верни списком с дефисами."
        ).format(n=n, lang=lang or self._settings.default_language)
        response = await self._chat_completion(prompt, text)
        items = [
            line.strip("-• \t ")
            for line in response.splitlines()
            if line.strip()
        ]
        return [item for item in items if item]

    async def moderate(self, text: str) -> dict[str, Any]:
        """Выполнить модерацию контента."""

        client = await self._ensure_client()
        result = await client.moderations.create(
            model="omni-moderation-latest",
            input=text,
        )
        moderation = result.results[0]
        if hasattr(moderation, "model_dump"):
            return moderation.model_dump()
        if isinstance(moderation, dict):
            return moderation
        return {
            key: getattr(moderation, key)
            for key in dir(moderation)
            if not key.startswith("_")
        }

    async def transcribe_audio(
        self,
        path: str | Path,
        *,
        lang: str | None = None,
    ) -> str:
        """Расшифровать аудиофайл с помощью Whisper."""

        client = await self._ensure_client()
        language = lang or self._settings.default_language
        file_path = Path(path)
        with file_path.open("rb") as handle:
            result = await client.audio.transcriptions.create(
                model=self._settings.whisper_model,
                file=handle,
                language=language,
                response_format="text",
            )
        if isinstance(result, str):
            return result.strip()
        text = getattr(result, "text", "")
        if text:
            return str(text).strip()
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        return ""

    async def generate_cover(
        self,
        title: str,
        *,
        style_hint: str | None = None,
    ) -> dict[str, Any]:
        """Сгенерировать изображение-обложку для публикации."""

        client = await self._ensure_client()
        prompt = "минималистичная обложка по теме: {title}".format(title=title)
        if style_hint:
            prompt = f"{prompt}. Стиль: {style_hint}"
        response = await client.images.generate(
            model=self._settings.image_model,
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
        data = response.data[0]
        url = getattr(data, "url", None) if not isinstance(data, dict) else data.get("url")
        b64 = getattr(data, "b64_json", None) if not isinstance(data, dict) else data.get("b64_json")
        return {"url": url, "b64_json": b64}

    async def author_rewrite(
        self,
        base_summary: str,
        author_notes: str,
        *,
        lang: str | None = None,
    ) -> str:
        """Переписать текст с учётом комментариев автора."""

        prompt = (
            "Перепиши текст в формате заметки от первого лица,"
            " опираясь на комментарии автора."
            " Сохрани деловой стиль и русский язык."
        )
        if lang:
            prompt = (
                "Перепиши текст на языке {lang} в стиле личной заметки,"
                " учитывая комментарии автора."
            ).format(lang=lang)
        response = await self._chat_completion(
            prompt,
            f"Оригинальное саммари:\n{base_summary}\n\nКомментарий автора:\n{author_notes}",
        )
        return _normalize_text(response)

    async def _chat_completion(self, system_prompt: str, user_content: str) -> str:
        client = await self._ensure_client()
        response = await client.chat.completions.create(
            model=self._settings.text_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        choice = response.choices[0].message
        content = getattr(choice, "content", "")
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content or "")

    async def _ensure_client(self) -> "AsyncOpenAI":
        async with self._lock:
            if self._client is None:
                if not self._settings.api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured")
                module = importlib.import_module("openai")
                async_openai_cls = getattr(module, "AsyncOpenAI", None)
                if async_openai_cls is None:
                    raise RuntimeError("AsyncOpenAI class is unavailable in openai package")
                self._client = async_openai_cls(api_key=self._settings.api_key)
            return self._client


_global_client: OpenAIClient | None = None
_global_lock = asyncio.Lock()


async def get_openai_client() -> OpenAIClient:
    """Лениво инициализировать общий экземпляр клиента."""

    global _global_client
    async with _global_lock:
        if _global_client is None:
            _global_client = OpenAIClient()
        return _global_client


def configure_openai_client(client: OpenAIClient | None) -> None:
    """Переопределить глобальный клиент (удобно для тестов)."""

    global _global_client
    _global_client = client


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


__all__ = [
    "OpenAIClient",
    "OpenAISettings",
    "configure_openai_client",
    "get_openai_client",
]
