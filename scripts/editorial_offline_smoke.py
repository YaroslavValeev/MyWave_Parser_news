"""Exercise the editorial workflow in temporary SQLite with all transports mocked."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)


def progress(phase, *, changed=None):
    print(json.dumps({"smoke_phase": phase, "source_changed": changed}), flush=True)


progress("imports")
import httpx
from openai import AsyncOpenAI
from config.settings import config
from nlp.openai_client import OpenAIClient, OpenAISettings
from services.nlp_pipeline import reprocess_items
from services.publication import PublicationService
from storage.repository import AsyncNewsRepository, initialize_database
from telegram_bot.views import (
    build_review_card_html,
    handle_callback,
    save_owner_review_comment,
)
from utils.source_context import source_input_hash

progress("imports_complete")


async def run_case(db: Path, *, changed: bool):
    progress("database", changed=changed)
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    url = "https://article.example.test/news/1785"
    text = "Инструкция по подготовке двигателя PCM к зимнему хранению. " * 5
    item_id = await repo.create_item(
        {
            "source": "Telegram",
            "content": f"Подробности: [{url}]({url})",
            "link": "https://t.me/example/3048",
            "status": "review",
        }
    )
    other = await repo.create_item(
        {
            "source": "Other",
            "content": "Другой текст",
            "link": "https://article.example.test/other",
        }
    )
    await repo.save_nlp_results(
        item_id,
        summary="Посторонний чемпионат 2023",
        voice_file="owner.ogg",
        rewrite_guidance="Мой тон",
    )
    await repo.upsert_author_notes(item_id, "Проверю свой двигатель перед зимой.")

    async def retrieve(item, hosts):
        return {
            "version": 1,
            "requested_url": url,
            "final_url": url,
            "title": "Зимнее хранение PCM",
            "text": text,
            "input_sha256": source_input_hash(item),
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        content = (
            "- Как подготовить двигатель?\n- Когда начинать подготовку?"
            if "Сформулируй" in body["messages"][0]["content"]
            else "Инструкция по подготовке двигателя PCM к зимнему хранению."
        )
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with AsyncOpenAI(
        api_key="offline-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as sdk:
        ai = OpenAIClient(
            OpenAISettings(
                "offline-test", "gpt-4o-mini", "whisper-1", "gpt-image-1", "ru"
            ),
            client=sdk,
        )
        ai.generate_cover = AsyncMock(return_value={})
        ai.moderate = AsyncMock(return_value={"flagged": False})
        with (
            patch.object(config, "SOURCE_ARTICLE_FETCH_ENABLED", True),
            patch.object(
                config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("article.example.test",)
            ),
            patch("services.source_article.retrieve_article", retrieve),
        ):
            progress("nlp", changed=changed)
            assert await reprocess_items([item_id], repository=repo, client=ai) == 1
    assert all(r["model"] == "gpt-4o-mini" for r in requests)
    assert all(r["messages"][1]["content"] == text for r in requests)
    assert (await repo.get_item(other))["status"] == "new"
    nlp = await repo.get_nlp_results(item_id)
    assert nlp["voice_file"] == "owner.ogg" and nlp["rewrite_guidance"] == "Мой тон"
    assert "Посторонний чемпионат" not in build_review_card_html(
        await repo.get_item(item_id), nlp
    )
    query = MagicMock()
    query.from_user.id, query.from_user.username = 1, "offline_owner"
    query.answer, query.message.answer, query.message.edit_text = (
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
    )
    with (
        patch("telegram_bot.views.sync_owner_comment", AsyncMock(return_value=False)),
        patch("telegram_bot.views.sync_final_text", AsyncMock(return_value=False)),
        patch(
            "telegram_bot.views.maybe_autoupload_local_cover_and_sync_sheet",
            AsyncMock(return_value=None),
        ),
        patch("telegram_bot.views.sync_publication_queue", AsyncMock()),
        patch("telegram_bot.views._offer_next_review", AsyncMock()),
        patch("services.publication.sync_publication_result", AsyncMock()),
    ):
        progress("owner_comment", changed=changed)
        await save_owner_review_comment(
            repo,
            item_id,
            "Проверю свой двигатель перед зимой.",
            user_id=1,
            username="offline_owner",
        )
        progress("owner_approval", changed=changed)
        await handle_callback(repo, query, {"action": "approve", "item_id": item_id})
        await handle_callback(
            repo, query, {"action": "publish_now", "item_id": item_id}
        )
        if changed:
            await repo.update_item_content(
                item_id, "https://article.example.test/news/9999"
            )
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
        service = PublicationService(repo, bot, channel_id="offline-test")
        progress("publication", changed=changed)
        assert await service.publish_pending(limit=1) == (0 if changed else 1)
        assert bot.send_message.await_count == (0 if changed else 1)
        if not changed:
            sent = bot.send_message.await_args.kwargs["text"]
            assert (
                "PCM" in sent and "чемпионат" not in sent.lower() and "Проверю" in sent
            )
    assert (await repo.get_publication_by_item(item_id) is None) == changed


async def main():
    # A mistakenly unmocked network call must fail before reaching a real service.
    with (
        tempfile.TemporaryDirectory(prefix="mywave-offline-smoke-") as directory,
        patch(
            "socket.socket.connect",
            side_effect=AssertionError("real_network_forbidden"),
        ),
        patch(
            "socket.socket.connect_ex",
            side_effect=AssertionError("real_network_forbidden"),
        ),
        patch.object(config, "OWNER_POST_USE_LLM_REWRITE", False),
        patch.object(
            PublicationService,
            "_hydrate_media",
            AsyncMock(return_value=None),
            create=True,
        ),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real_http_forbidden"),
        ),
        patch(
            "httpx.AsyncHTTPTransport.handle_async_request",
            side_effect=AssertionError("real_api_forbidden"),
        ),
    ):
        await run_case(Path(directory) / "success.db", changed=False)
        await run_case(Path(directory) / "changed.db", changed=True)
    print(
        json.dumps(
            {
                "offline_smoke": "ok",
                "cases": 2,
                "real_telegram_sends": 0,
                "real_openai_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=35))
    except Exception as exc:
        print(json.dumps({"offline_smoke": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1)
