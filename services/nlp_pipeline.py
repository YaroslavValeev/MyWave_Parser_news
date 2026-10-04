"""Асинхронные операции обработки NLP-очереди."""

from __future__ import annotations

import logging
from typing import Iterable

from config.settings import config
from nlp.openai_client import OpenAIClient, get_openai_client
from nlp.routing import DISCARD, PUBLISH, REVIEW, decide_route
from storage.data import get_repository
from storage.repository import AsyncNewsRepository
from utils.item_context import get_item_text_context, missing_text_context_summary

LOGGER = logging.getLogger(__name__)

STATUS_PROCESSING = "processing"
STATUS_ERROR = "error"
# NLP никогда не публикует сам: даже «хороший» материал → review (нужен комментарий Owner).
STATUS_MAP = {
    PUBLISH: "review",
    REVIEW: "review",
    DISCARD: "discarded",
}


async def process_nlp_queue(
    *,
    repository: AsyncNewsRepository | None = None,
    client: OpenAIClient | None = None,
    batch_size: int = 10,
    lang: str | None = None,
) -> int:
    """Обработать элементы со статусом ``new`` и вернуть количество успехов."""

    repo = repository or await get_repository()
    ai_client = client or await get_openai_client()
    items = await repo.list_items_by_status(["new"], limit=batch_size)
    if not items:
        return 0

    return await _process_items(
        items, repository=repo, client=ai_client, lang=lang or config.NL_LANG
    )


async def _process_items(
    items: Iterable[dict],
    *,
    repository: AsyncNewsRepository,
    client: OpenAIClient,
    lang: str,
) -> int:
    """Обработать переданные записи, не выбирая другие элементы общей очереди."""
    repo = repository
    ai_client = client
    processed = 0
    target_lang = lang
    for item in items:
        item_id = item["id"]
        try:
            await repo.update_status(item_id, STATUS_PROCESSING)
            text = get_item_text_context(item)
            if not text:
                await repo.save_nlp_results(
                    item_id,
                    summary=missing_text_context_summary(item),
                    questions=[],
                    decision=REVIEW,
                    extra={"sanitized_text": "", "source_context_missing": True},
                )
                await repo.update_status(item_id, REVIEW)
                await repo.log_event(
                    item_id,
                    "warning",
                    "nlp_skipped_missing_text_context",
                    {"status": REVIEW},
                )
                processed += 1
                continue

            summary = await ai_client.summarize(text, lang=target_lang)
            questions = await ai_client.gen_questions(text, lang=target_lang)
            moderation = await ai_client.moderate(text)
            decision = decide_route(summary, questions, moderation)
            extra_payload: dict[str, object] = {
                "sanitized_text": text,
            }
            try:
                from services.semantic_dedup import maybe_attach_event_id

                event_id = maybe_attach_event_id(item, {"summary": summary})
                if event_id:
                    extra_payload["event_id"] = event_id
            except Exception:  # noqa: BLE001
                LOGGER.debug("semantic event_id attach skipped", exc_info=True)
            cover_prompt = item.get("title") or summary
            if cover_prompt:
                try:
                    cover_payload = await ai_client.generate_cover(str(cover_prompt))
                except Exception as cover_error:  # noqa: BLE001
                    await repo.log_event(
                        item_id,
                        "warning",
                        "cover_generation_failed",
                        {
                            "error": str(cover_error),
                        },
                    )
                else:
                    if cover_payload and any(cover_payload.values()):
                        extra_payload["cover"] = cover_payload
                        await repo.log_event(
                            item_id,
                            "info",
                            "cover_generated",
                            {
                                "has_url": bool(cover_payload.get("url")),
                                "has_base64": bool(cover_payload.get("b64_json")),
                            },
                        )

            await repo.save_nlp_results(
                item_id,
                summary=summary,
                questions=questions,
                decision=decision,
                moderation=moderation,
                extra=extra_payload,
            )
            new_status = STATUS_MAP.get(decision, REVIEW)
            await repo.update_status(item_id, new_status)
            await repo.log_event(
                item_id,
                "info",
                "nlp_processed",
                {
                    "decision": decision,
                    "status": new_status,
                },
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001
            await repo.update_status(item_id, STATUS_ERROR)
            await repo.log_event(
                item_id,
                "error",
                "nlp_processing_failed",
                {
                    "error": str(exc),
                },
            )
            LOGGER.exception("Failed to process item %s", item_id)
    return processed


async def reprocess_items(
    item_ids: Iterable[int],
    *,
    repository: AsyncNewsRepository | None = None,
    client: OpenAIClient | None = None,
    lang: str | None = None,
) -> int:
    """Принудительно обработать конкретные элементы."""

    repo = repository or await get_repository()
    ai_client = client or await get_openai_client()
    processed = 0
    target_lang = lang or config.NL_LANG
    for item_id in dict.fromkeys(item_ids):
        item = await repo.get_item(item_id)
        if not item:
            continue
        processed += await _process_items(
            [item],
            repository=repo,
            client=ai_client,
            lang=target_lang,
        )
    return processed


__all__ = ["process_nlp_queue", "reprocess_items", "STATUS_PROCESSING", "STATUS_ERROR"]
