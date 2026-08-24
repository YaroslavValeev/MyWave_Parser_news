"""
Автотесты контура Owner Review: колбэки карточки и сохранение комментария.

Реальный Telegram API не используется (моки CallbackQuery).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from telegram_bot.keyboards import owner_review_card_markup

import pytest
from aiogram.exceptions import TelegramBadRequest

from config.settings import config
from storage.repository import AsyncNewsRepository, initialize_database
from telegram_bot.views import (
    build_review_card_html,
    format_review_queue_summary,
    handle_author_rewrite,
    handle_callback,
    review_queue_keyboard,
    save_owner_review_comment,
    show_review_item,
    show_review_item_card,
)


def _make_query(
    *,
    user_id: int = 1001,
    username: str | None = "owner_test",
    with_message: bool = True,
):
    q = MagicMock()
    q.from_user = MagicMock()
    q.from_user.id = user_id
    q.from_user.username = username
    q.answer = AsyncMock()
    if with_message:
        q.message = MagicMock()
        q.message.edit_text = AsyncMock()
        q.message.answer = AsyncMock()
    else:
        q.message = None
    return q


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "or.sqlite"
    asyncio.run(initialize_database(db))
    return AsyncNewsRepository(db)


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_approve_discard_defer_publish(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://example.com/a",
            "status": "review",
        }
    )
    q = _make_query()

    await handle_callback(repo, q, {"action": "approve", "item_id": item_id})
    assert (await repo.get_item(item_id))["status"] == "review"
    assert await repo.get_last_log(item_id, "owner_action_blocked_missing_author_note")
    assert q.answer.await_args.kwargs.get("show_alert") is True

    await repo.upsert_author_notes(item_id, "Есть комментарий")
    await handle_callback(repo, q, {"action": "approve", "item_id": item_id})
    assert (await repo.get_item(item_id))["status"] == "approved"
    assert await repo.get_last_log(item_id, "owner_approve")

    await repo.update_status(item_id, "review")
    await handle_callback(repo, q, {"action": "discard", "item_id": item_id})
    assert (await repo.get_item(item_id))["status"] == "discarded"
    assert await repo.get_last_log(item_id, "owner_discard")

    i2 = await repo.create_item(
        {
            "source": "t",
            "title": "y",
            "content": "d",
            "link": "https://example.com/b",
            "status": "review",
        }
    )
    await handle_callback(repo, q, {"action": "defer", "item_id": i2})
    assert (await repo.get_item(i2))["status"] == "deferred"
    assert await repo.get_last_log(i2, "owner_defer")

    i3 = await repo.create_item(
        {
            "source": "t",
            "title": "z",
            "content": "e",
            "link": "https://example.com/c",
            "status": "review",
        }
    )
    await handle_callback(repo, q, {"action": "publish_now", "item_id": i3})
    assert (await repo.get_item(i3))["status"] == "review"
    assert await repo.get_last_log(i3, "owner_action_blocked_missing_author_note")
    await repo.upsert_author_notes(i3, "Публикуем с этим мнением")
    await handle_callback(repo, q, {"action": "publish_now", "item_id": i3})
    assert (await repo.get_item(i3))["status"] == "ready_to_publish"
    assert await repo.get_last_log(i3, "owner_publish_queue")


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_open_source_url(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://news.example/article",
            "status": "review",
        }
    )
    q = _make_query()
    await handle_callback(repo, q, {"action": "open_source", "item_id": item_id})
    q.answer.assert_awaited()
    call_kw = q.answer.await_args.kwargs
    assert call_kw.get("url") == "https://news.example/article"
    assert await repo.get_last_log(item_id, "owner_open_source")


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_open_source_fallback_on_telegram_error(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://fallback.example/p",
            "status": "review",
        }
    )
    q = _make_query()
    q.answer = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=MagicMock(), message="bad callback url"),
            None,
        ]
    )
    await handle_callback(repo, q, {"action": "open_source", "item_id": item_id})
    assert q.answer.await_count >= 1
    q.message.answer.assert_awaited()


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_retry_nlp_mocked(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://example.com/r",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="s", decision="review")
    q = _make_query()
    with patch("services.nlp_pipeline.reprocess_items", new_callable=AsyncMock, return_value=1):
        await handle_callback(repo, q, {"action": "retry_nlp", "item_id": item_id})
    assert await repo.get_last_log(item_id, "owner_retry_nlp")


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_retry_nlp_rejects_wrong_status(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://example.com/x",
            "status": "approved",
        }
    )
    q = _make_query()
    await handle_callback(repo, q, {"action": "retry_nlp", "item_id": item_id})
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_save_owner_review_comment(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "x",
            "content": "c",
            "link": "https://example.com/c",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="sum", decision="review")
    await save_owner_review_comment(
        repo,
        item_id,
        "  Моё мнение  ",
        user_id=42,
        username="u",
    )
    nlp = await repo.get_nlp_results(item_id)
    assert nlp.get("author_notes") == "Моё мнение"
    assert await repo.get_last_log(item_id, "owner_author_note")


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_show_review_item_sends_card_and_keyboard(repo: AsyncNewsRepository):
    """Тот же путь, что «Ревью» после list_review_queue: текст карточки + клавиатура."""
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "Headline",
            "content": "c",
            "link": "https://example.com/card",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Кратко", decision="review")
    msg = MagicMock()
    msg.answer = AsyncMock()
    await show_review_item(repo, item_id, msg)
    msg.answer.assert_awaited()
    text = msg.answer.await_args[0][0]
    assert "Headline" in text and "Кратко" in text and "example.com" in text
    assert "Исходный текст" in text and "c" in text
    assert msg.answer.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_show_review_item_card_uses_telegram_text_fallback(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "telegram:test",
            "title": "Headline",
            "content": "",
            "link": "https://t.me/test/123",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Кратко", decision="review")
    msg = MagicMock()
    msg.answer = AsyncMock()
    with (
        patch("telegram_bot.views._send_review_media_preview", AsyncMock()),
        patch("telegram_bot.views._download_telegram_review_text", AsyncMock(return_value="Текст из Telegram")),
    ):
        await show_review_item_card(repo, item_id, msg)
    text = msg.answer.await_args[0][0]
    assert "Текст из Telegram" in text
    assert "в базе пусто" not in text
    persisted = await repo.get_item(item_id)
    assert persisted is not None
    assert persisted.get("content") == "Текст из Telegram"


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_show_review_item_card_uses_external_text_fallback(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "rss:test",
            "title": "Headline",
            "content": "",
            "link": "https://example.com/article",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Кратко", decision="review")
    msg = MagicMock()
    msg.answer = AsyncMock()
    with (
        patch("telegram_bot.views._send_review_media_preview", AsyncMock()),
        patch("telegram_bot.views._download_external_review_text", AsyncMock(return_value="Текст из сайта")),
    ):
        await show_review_item_card(repo, item_id, msg)
    text = msg.answer.await_args[0][0]
    assert "Текст из сайта" in text
    assert "в базе пусто" not in text
    persisted = await repo.get_item(item_id)
    assert persisted is not None
    assert persisted.get("content") == "Текст из сайта"


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_open_shows_card(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "Open me",
            "content": "c",
            "link": "https://example.com/o",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="S", decision="review")
    q = _make_query()
    await handle_callback(repo, q, {"action": "open", "item_id": item_id})
    q.answer.assert_awaited()
    q.message.answer.assert_awaited()


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_open_missing_item(repo: AsyncNewsRepository):
    q = _make_query()
    await handle_callback(repo, q, {"action": "open", "item_id": 999999})
    assert q.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.owner_review
def test_owner_review_card_markup_includes_all_prefixes_no_duplicate_blocks():
    mk = owner_review_card_markup(7, include_publish=True)
    flat = [b for row in mk.inline_keyboard for b in row]
    packed = [b.callback_data for b in flat if b.callback_data]
    assert any(p.startswith("rev:") for p in packed)
    assert any(p.startswith("prv:") for p in packed)
    assert any(p.startswith("auth:") for p in packed)
    # Одна компактная клавиатура: без второго блока «публикация/отклонить» из author_decision
    assert any(p == "rev:add_cover:7" for p in packed)
    assert any(p == "rev:retry_media:7" for p in packed)
    assert len(packed) == 11


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_author_rewrite_updates_summary(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "T",
            "content": "c",
            "link": "https://example.com/rw",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Исходное саммари", decision="review")
    await repo.upsert_author_notes(item_id, "Сделай короче")

    mock_client = MagicMock()
    mock_client.author_rewrite = AsyncMock(return_value="Короткая версия")

    q = _make_query()
    media_preview = AsyncMock()
    with (
        patch("nlp.openai_client.get_openai_client", AsyncMock(return_value=mock_client)),
        patch.object(config, "OPENAI_API_KEY", "test-key"),
        patch.dict("os.environ", {"PYTEST_CURRENT_TEST": ""}),
        patch("telegram_bot.views._send_review_media_preview", media_preview),
    ):
        await handle_author_rewrite(repo, q, item_id)

    q.answer.assert_awaited()
    media_preview.assert_awaited_once()
    nlp = await repo.get_nlp_results(item_id)
    assert nlp.get("summary") == "Исходное саммари"
    assert nlp.get("merged_text") == "Короткая версия"
    mock_client.author_rewrite.assert_awaited_once()
    args = mock_client.author_rewrite.await_args
    assert args.args[0] == "c"
    assert args.args[1] == "Сделай короче"
    assert args.kwargs["base_summary"] == "Исходное саммари"
    q.message.answer.assert_awaited()
    card = q.message.answer.await_args[0][0]
    assert "Короткая версия" in card and "Исходный текст" in card


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_save_owner_review_comment_forces_regeneration(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "T",
            "content": "Исходный текст материала",
            "link": "https://example.com/comment-refresh",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Саммари", merged_text="Старая финальная версия")

    mock_client = MagicMock()
    mock_client.author_rewrite = AsyncMock(return_value="Новая финальная версия")

    with (
        patch("nlp.openai_client.get_openai_client", AsyncMock(return_value=mock_client)),
        patch.object(config, "OPENAI_API_KEY", "test-key"),
        patch.object(config, "OWNER_POST_USE_LLM_REWRITE", True),
        patch.dict("os.environ", {"PYTEST_CURRENT_TEST": ""}),
    ):
        merged = await save_owner_review_comment(
            repo,
            item_id,
            "Новый комментарий owner",
            user_id=1001,
            username="owner_test",
        )

    assert merged == "Новая финальная версия"
    nlp = await repo.get_nlp_results(item_id)
    assert nlp.get("merged_text") == "Новая финальная версия"
    mock_client.author_rewrite.assert_awaited_once()


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_author_rewrite_requires_notes(repo: AsyncNewsRepository):
    item_id = await repo.create_item(
        {
            "source": "t",
            "title": "T",
            "content": "c",
            "link": "https://example.com/rw2",
            "status": "review",
        }
    )
    await repo.save_nlp_results(item_id, summary="Только саммари", decision="review")
    q = _make_query()
    with patch("telegram_bot.views.get_openai_client", AsyncMock()):
        await handle_author_rewrite(repo, q, item_id)
    assert q.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.owner_review
def test_format_review_queue_summary_and_keyboard():
    items = [
        {"id": 1, "title": "A" * 100, "status": "review"},
        {"id": 2, "title": "B", "status": "new"},
    ]
    text = format_review_queue_summary(items)
    assert "#1" in text and "#2" in text and "на ревью" in text
    kb = review_queue_keyboard(items)
    assert kb.inline_keyboard


@pytest.mark.owner_review
def test_build_review_card_hides_title_only_summary_for_empty_context():
    item = {
        "id": 101,
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "",
        "link": "https://t.me/talktofish/347",
    }
    nlp = {
        "summary": "Кристина Колесникова — российская певица и композитор...",
        "extra": {"sanitized_text": "Cristina Kolesnikova"},
    }

    card = build_review_card_html(item, nlp, link_as_anchor=False)

    assert "Пост из ДИАЛОГИ О РЫБАЛКЕ #347" in card
    assert "российская певица" not in card
    assert "скрыто: в базе нет текстового контекста" in card


@pytest.mark.owner_review
def test_build_review_card_normalizes_english_summary_to_russian():
    item = {
        "id": 102,
        "source": "rss:Wakeboarding Mag",
        "title": "Wakeboarding, Wakeboard Gear, Videos, Tips, Photos | Wakeboarding Mag",
        "content": "Wakeboarding Magazine covers the latest in wakeboarding gear, videos, tips, photos, boats, news, and so much more.",
        "link": "https://www.wakeboardingmag.com/",
    }
    nlp = {
        "summary": "Wakeboarding Magazine covers the latest in wakeboarding gear, videos, tips, photos, boats, news, and so much more.",
    }

    card = build_review_card_html(item, nlp, link_as_anchor=False)
    summary_section = card.split("<b>Саммари (NLP)</b>", 1)[1].split("\n\nИсточник", 1)[0]

    assert "Wakeboarding Magazine covers the latest" not in summary_section
    assert "Материал Wakeboarding Magazine о вейкбординге" in summary_section


@pytest.mark.owner_review
def test_build_review_card_shows_final_version_when_present():
    item = {
        "id": 11,
        "source": "telegram:chan",
        "title": "T",
        "content": "Body",
        "link": "https://example.com/11",
    }
    nlp = {
        "summary": "Короткое саммари",
        "merged_text": "Финальная версия от автора",
        "author_notes": "Добавить акцент на событии",
    }

    card = build_review_card_html(item, nlp, link_as_anchor=False)

    assert "Финальная версия" in card
    assert "Финальная версия от автора" in card
    assert "Ваш комментарий" not in card
    assert card.index("Финальная версия от автора") < card.index("Исходный текст")


@pytest.mark.owner_review
@pytest.mark.asyncio
async def test_handle_callback_invalid_payload(repo: AsyncNewsRepository):
    q = _make_query()
    await handle_callback(repo, q, {"action": "approve", "item_id": 0})
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs.get("show_alert") is True
