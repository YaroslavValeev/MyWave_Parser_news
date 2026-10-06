"""Integrated Owner workflow with SQLite and mocked external transports."""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import config
from services.nlp_pipeline import reprocess_items
from services.publication import PublicationService
from services import source_article
from storage.repository import AsyncNewsRepository, initialize_database
from telegram_bot.views import (
    build_review_card_html,
    handle_callback,
    save_owner_review_comment,
)
from utils.source_context import source_input_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("source_changed", [False, True])
async def test_selected_link_to_owner_review_to_publication_without_network(
    tmp_path, monkeypatch, source_changed
):
    url = "https://news.example.test/news/1785"
    text = "В статье рассказано о подготовке двигателя PCM к зимнему хранению. " * 5
    db = tmp_path / "data.db"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    selected = await repo.create_item(
        {
            "source": "Telegram",
            "link": "https://t.me/Channel/3048",
            "content": url,
            "status": "review",
            "images": "https://cdn.example.test/pcm.jpg",
        }
    )
    other = await repo.create_item(
        {
            "source": "Other",
            "content": "Другой материал",
            "link": "https://news.example.test/other",
            "status": "new",
        }
    )
    await repo.save_nlp_results(
        selected,
        summary="Чемпионат Москвы 2023",
        voice_file="owner.ogg",
        rewrite_guidance="Сохранить мой тон",
    )
    await repo.upsert_author_notes(selected, "Перед зимой проверю свой двигатель.")
    before = await repo.get_item(selected)
    before_nlp = await repo.get_nlp_results(selected)
    assert "Чемпионат Москвы" not in build_review_card_html(before, before_nlp)

    async def retrieve(item, hosts):
        return {
            "version": 1,
            "requested_url": url,
            "final_url": url,
            "title": "Двигатель PCM: зимнее хранение",
            "text": text,
            "input_sha256": source_input_hash(item),
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    monkeypatch.setattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", True)
    monkeypatch.setattr(config, "SOURCE_ARTICLE_ALLOWED_HOSTS", ("news.example.test",))
    monkeypatch.setattr(source_article, "retrieve_article", retrieve)
    monkeypatch.setattr("telegram_bot.views.sync_publication_queue", AsyncMock())
    monkeypatch.setattr("telegram_bot.views._offer_next_review", AsyncMock())
    monkeypatch.setattr("services.publication.sync_publication_result", AsyncMock())
    ai = AsyncMock()
    ai.summarize.return_value = (
        "Материал посвящён подготовке двигателя PCM к зимнему хранению."
    )
    ai.gen_questions.return_value = ["Как подготовить двигатель к зиме?"]
    ai.moderate.return_value = {"flagged": False}
    ai.generate_cover.return_value = {}
    assert await reprocess_items([selected], repository=repo, client=ai) == 1
    ai.summarize.assert_awaited_once_with(text, lang="ru")
    assert (await repo.get_item(other))[
        "status"
    ] == "new" and await repo.get_nlp_results(other) is None
    nlp = await repo.get_nlp_results(selected)
    assert (
        nlp["voice_file"] == "owner.ogg"
        and nlp["rewrite_guidance"] == "Сохранить мой тон"
    )
    await save_owner_review_comment(
        repo,
        selected,
        "Проверю подготовку своего двигателя.",
        user_id=1001,
        username="test_owner",
    )
    card = build_review_card_html(
        await repo.get_item(selected), await repo.get_nlp_results(selected)
    )
    assert "PCM" in card and "Чемпионат Москвы" not in card
    query = MagicMock()
    query.from_user.id = 1001
    query.from_user.username = "test_owner"
    query.answer = AsyncMock()
    query.message.answer = AsyncMock()
    query.message.edit_text = AsyncMock()
    await handle_callback(repo, query, {"action": "approve", "item_id": selected})
    assert (await repo.get_item(selected))["status"] == "approved"
    await handle_callback(repo, query, {"action": "publish_now", "item_id": selected})
    assert (await repo.get_item(selected))["status"] == "ready_to_publish"
    if source_changed:
        await repo.update_item_content(selected, "https://news.example.test/news/9999")
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=123))
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=124))
    svc = PublicationService(repo, bot, channel_id="offline-test")
    assert await svc.publish_pending(limit=1) == (0 if source_changed else 1)
    if source_changed:
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        assert await repo.get_publication_by_item(selected) is None
    else:
        bot.send_photo.assert_awaited_once()
        caption = bot.send_photo.await_args.kwargs["caption"]
        assert "PCM" in caption and "Чемпионат" not in caption and "Проверю" in caption
        assert (await repo.get_item(selected))["status"] == "published"
        assert (await repo.get_publication_by_item(selected))["message_id"] == "123"
        assert (await repo.get_item(selected))["content"] == url
