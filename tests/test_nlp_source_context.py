from unittest.mock import AsyncMock

import pytest

from services.nlp_pipeline import process_nlp_queue, reprocess_items
from services.publication import PublicationService
from storage.repository import AsyncNewsRepository, initialize_database
from telegram_bot.views import build_review_card_html, handle_author_rewrite
from utils.owner_content import ensure_merged_owner_post


def _client():
    client = AsyncMock()
    client.summarize.side_effect = lambda text, **kwargs: f"Резюме: {text}"
    client.gen_questions.return_value = ["Когда нужно готовить двигатель к хранению?"]
    client.moderate.return_value = {"flagged": False, "categories": {}}
    client.generate_cover.return_value = {}
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "https://wakeflot.ru/news/1785"])
async def test_no_facts_are_generated_without_source_text(tmp_path, content):
    db_file = tmp_path / "missing-context.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "Wakeflot",
            "title": "Wakeflot",
            "content": content,
            "link": "https://t.me/Wakeflot/3048",
            "status": "new",
        }
    )
    client = _client()

    assert await process_nlp_queue(repository=repo, client=client) == 1

    client.summarize.assert_not_awaited()
    client.gen_questions.assert_not_awaited()
    client.moderate.assert_not_awaited()
    client.generate_cover.assert_not_awaited()
    assert (await repo.get_item(item_id))["status"] == "review"
    nlp = await repo.get_nlp_results(item_id)
    assert "нет текстового контента" in nlp["summary"]
    assert nlp["extra"]["source_context_missing"] is True
    assert await repo.get_last_log(item_id, "nlp_skipped_missing_text_context")


@pytest.mark.asyncio
async def test_regeneration_only_processes_selected_items(tmp_path):
    db_file = tmp_path / "selected-items.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    selected_id = await repo.create_item(
        {
            "source": "Wakeflot",
            "title": "Консервация PCM",
            "content": "Подготовка двигателей PCM к зимнему хранению.",
            "link": "https://t.me/Wakeflot/3048",
            "status": "review",
        }
    )
    pending_id = await repo.create_item(
        {
            "source": "Другой источник",
            "title": "Соревнования",
            "content": "Новость о соревнованиях другой очереди.",
            "link": "https://example.com/other",
            "status": "new",
        }
    )
    await repo.upsert_author_notes(selected_id, "Мой комментарий")
    client = _client()

    assert await reprocess_items([selected_id], repository=repo, client=client) == 1

    client.summarize.assert_awaited_once_with(
        "Подготовка двигателей PCM к зимнему хранению.", lang="ru"
    )
    nlp = await repo.get_nlp_results(selected_id)
    assert "PCM" in nlp["summary"]
    assert nlp["author_notes"] == "Мой комментарий"
    assert (await repo.get_item(selected_id))["status"] == "review"
    assert (await repo.get_item(pending_id))["status"] == "new"
    assert await repo.get_nlp_results(pending_id) is None


@pytest.mark.parametrize("merged_text", ["", "Чемпионат России: финальная версия."])
def test_link_only_card_and_caption_hide_unrelated_old_summary(merged_text):
    item = {
        "id": 3048,
        "source": "Wakeflot",
        "title": "Wakeflot",
        "content": "https://wakeflot.ru/news/1785",
        "link": "https://t.me/Wakeflot/3048",
    }
    nlp = {
        "summary": "В Москве 14 октября 2023 года прошёл чемпионат России по вейкборду.",
        "author_notes": "Посмотрите источник перед публикацией.",
        "merged_text": merged_text,
        "extra": {"sanitized_text": item["content"]},
    }

    card = build_review_card_html(item, nlp)
    caption = PublicationService._build_caption(item, nlp)

    assert "чемпионат россии" not in card.casefold()
    assert "чемпионат россии" not in caption.casefold()
    assert "https://wakeflot.ru/news/1785" in card
    assert "скрыто: в базе нет текстового контекста" in card
    assert "нет текстового контента" in caption


@pytest.mark.asyncio
async def test_owner_comment_does_not_turn_ungrounded_summary_into_a_final_post():
    item = {"content": "https://wakeflot.ru/news/1785"}
    nlp = {
        "summary": "В Москве прошёл чемпионат России по вейкборду.",
        "author_notes": "Мой комментарий о подготовке двигателя.",
    }
    assert await ensure_merged_owner_post(item, nlp, force=True) == ""


@pytest.mark.asyncio
async def test_publication_of_link_only_untrusted_summary_is_blocked(tmp_path):
    db_file = tmp_path / "blocked-publication.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "Wakeflot",
            "title": "Wakeflot",
            "content": "https://wakeflot.ru/news/1785",
            "link": "https://t.me/Wakeflot/3048",
            "status": "approved",
        }
    )
    await repo.save_nlp_results(
        item_id,
        summary="В Москве прошёл чемпионат России по вейкборду.",
        merged_text="В Москве прошёл чемпионат России по вейкборду. Мой комментарий.",
    )
    await repo.upsert_author_notes(item_id, "Мой комментарий")
    bot = AsyncMock()

    assert await PublicationService(repo, bot, channel_id="42").publish_pending() == 0

    bot.send_message.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
    assert (await repo.get_item(item_id))["status"] == "review"
    assert await repo.get_last_log(item_id, "publication_blocked_untrusted_summary")


@pytest.mark.asyncio
async def test_rewrite_button_cannot_approve_a_summary_generated_from_a_link(tmp_path):
    db_file = tmp_path / "blocked-rewrite.sqlite"
    await initialize_database(db_file)
    repo = AsyncNewsRepository(db_file)
    item_id = await repo.create_item(
        {
            "source": "Wakeflot",
            "title": "Wakeflot",
            "content": "https://wakeflot.ru/news/1785",
            "link": "https://t.me/Wakeflot/3048",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="В Москве прошёл чемпионат России.")
    await repo.upsert_author_notes(item_id, "Мой комментарий")
    query = AsyncMock()

    await handle_author_rewrite(repo, query, item_id)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert "нет текста источника" in query.answer.await_args.args[0]
    nlp = await repo.get_nlp_results(item_id)
    assert not nlp.get("merged_text")
    assert not (nlp.get("extra") or {}).get("owner_rewritten")
