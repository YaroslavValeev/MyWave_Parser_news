"""Owner edits are isolated, cancelable and honored by publication; no network."""

import html
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from config.settings import config
from services.publication import PublicationService
from storage.repository import AsyncNewsRepository, initialize_database
from telegram_bot.keyboards import (
    AuthorDecisionAction,
    author_decision_keyboard,
    owner_review_card_markup,
)
from telegram_bot.router import create_router
from telegram_bot.final_post_edit import FinalPostEditForm


@pytest_asyncio.fixture
async def editorial(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    await initialize_database(db)
    repo = AsyncNewsRepository(db)
    selected = await repo.create_item(
        {
            "title": "Зимнее хранение",
            "source": "test",
            "content": "Статья о зимнем хранении двигателя.",
            "link": "https://example.test/news/1",
            "status": "review",
        }
    )
    other = await repo.create_item(
        {
            "content": "Другой материал",
            "link": "https://example.test/news/2",
            "status": "review",
        }
    )
    await repo.save_nlp_results(
        selected,
        summary="Саммари источника.",
        questions=["Вопрос?"],
        decision="review",
        moderation={"flagged": False},
        extra={"keep": "media"},
        merged_text="Предыдущая финальная версия",
        voice_file="owner.ogg",
        rewrite_guidance="Мой тон",
    )
    await repo.upsert_author_notes(selected, "Мой отдельный комментарий.")
    monkeypatch.setattr(config, "OWNER_USER_ID", "1001")
    monkeypatch.setattr(config, "EDITORS_CHAT_ID", "")
    monkeypatch.setattr(config, "SOURCE_ARTICLE_FETCH_ENABLED", False)
    monkeypatch.setattr("telegram_bot.views.show_review_item_card", AsyncMock())
    monkeypatch.setattr("telegram_bot.views.sync_final_text", AsyncMock())
    monkeypatch.setattr("telegram_bot.views.sync_owner_comment", AsyncMock())
    monkeypatch.setattr(
        "telegram_bot.views.maybe_autoupload_local_cover_and_sync_sheet", AsyncMock()
    )
    router = create_router(repo, MagicMock())
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage, key=StorageKey(bot_id=1, chat_id=1001, user_id=1001)
    )
    yield repo, selected, other, router, state
    await storage.close()


def message(text, *, uid=1001):
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat={"id": uid, "type": "private"},
        from_user={"id": uid, "is_bot": False, "first_name": "Owner"},
        text=text,
    )


async def begin(router, state, item_id, monkeypatch, *, uid=1001):
    answer = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    query = CallbackQuery(
        id="offline",
        from_user={"id": uid, "is_bot": False, "first_name": "Owner"},
        chat_instance="offline",
        message=message("Карточка", uid=uid),
        data=AuthorDecisionAction(action="edit_final", item_id=item_id).pack(),
    )
    await router.callback_query.trigger(query, state=state)
    return answer, callback_answer


async def receive(router, state, text, *, uid=1001):
    await router.message.trigger(
        message(text, uid=uid),
        state=state,
        bot=MagicMock(),
        raw_state=await state.get_state(),
    )


def test_edit_button_is_visible_in_both_owner_keyboards():
    for markup in (owner_review_card_markup(626), author_decision_keyboard(626)):
        buttons = [b for row in markup.inline_keyboard for b in row]
        edit = next(b for b in buttons if b.text == "✏️ Редактировать финальный пост")
        assert AuthorDecisionAction.unpack(edit.callback_data).item_id == 626
        assert AuthorDecisionAction.unpack(edit.callback_data).action == "edit_final"


@pytest.mark.asyncio
async def test_routed_edit_preserves_other_fields_and_publishes_exact_manual_body(
    editorial, monkeypatch
):
    repo, selected, other, router, state = editorial
    before_item = await repo.get_item(selected)
    before_nlp = await repo.get_nlp_results(selected)
    before_other = await repo.get_item(other)
    answer, _ = await begin(router, state, selected, monkeypatch)
    assert await state.get_state() == FinalPostEditForm.waiting_text.state
    assert answer.await_args.args[0] == before_nlp["merged_text"]
    assert answer.await_args.kwargs["parse_mode"] is None
    assert (await repo.get_nlp_results(selected)) == before_nlp
    edited = "Мой заголовок\n\nТочный текст с M5/M6 & <важной оговоркой>.\n\nЛичный вывод владельца."
    await receive(router, state, edited)
    assert await state.get_state() is None
    after_nlp = await repo.get_nlp_results(selected)
    assert after_nlp["merged_text"] == edited
    for key in (
        "summary",
        "questions",
        "decision",
        "moderation",
        "voice_file",
        "rewrite_guidance",
        "author_notes",
    ):
        assert after_nlp[key] == before_nlp[key]
    assert after_nlp["extra"] == {
        **before_nlp["extra"],
        "owner_manual_final_text": True,
    }
    assert await repo.get_item(selected) == before_item
    assert await repo.get_item(other) == before_other
    caption = PublicationService._build_caption(before_item, after_nlp)
    assert caption.startswith(html.escape(edited) + "\n\n")
    assert (
        "Саммари источника" not in caption
        and "Мой отдельный комментарий" not in caption
    )
    assert "Источник</a>" in caption
    assert await repo.get_last_log(selected, "owner_final_text_edited")
    assert await repo.get_publication_by_item(selected) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["", "x" * 3501, "😀" * 1751], ids=["empty", "long", "emoji-units"]
)
async def test_invalid_input_keeps_session_and_draft(editorial, monkeypatch, invalid):
    repo, selected, _, router, state = editorial
    before = await repo.get_nlp_results(selected)
    await begin(router, state, selected, monkeypatch)
    await receive(router, state, invalid)
    assert await state.get_state() == FinalPostEditForm.waiting_text.state
    assert await repo.get_nlp_results(selected) == before


@pytest.mark.asyncio
async def test_cancel_and_denied_operator_do_not_write(editorial, monkeypatch):
    repo, selected, _, router, state = editorial
    before = await repo.get_nlp_results(selected)
    await begin(router, state, selected, monkeypatch, uid=9999)
    assert await state.get_state() is None
    await begin(router, state, selected, monkeypatch)
    await receive(router, state, "/cancel")
    assert await state.get_state() is None
    assert await repo.get_nlp_results(selected) == before
    await begin(router, state, selected, monkeypatch)
    await receive(router, state, "Чужая правка", uid=9999)
    assert await state.get_state() is None
    assert await repo.get_nlp_results(selected) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "nlp", "status", "publication"])
async def test_concurrent_changes_are_not_overwritten(editorial, change):
    repo, selected, _, _, _ = editorial
    token = (await repo.get_final_post_edit_snapshot(selected))["token"]
    if change == "source":
        await repo.update_item_content(selected, "Новый источник")
    elif change == "nlp":
        await repo.upsert_author_notes(selected, "Другой комментарий")
    elif change == "status":
        await repo.update_status(selected, "ready_to_publish")
    else:
        await repo.save_publication(selected, "offline", "1")
    before = await repo.get_nlp_results(selected)
    with pytest.raises(ValueError, match="final_post_(changed|not_editable)"):
        await repo.save_manual_final_post(selected, "Моя правка", expected_token=token)
    assert await repo.get_nlp_results(selected) == before
    assert not await repo.get_last_log(selected, "owner_final_text_edited")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["published", "discarded", "approved", "ready_to_publish", "processing", "new"],
)
async def test_queued_and_terminal_items_cannot_be_edited(editorial, status):
    repo, selected, _, _, _ = editorial
    await repo.update_status(selected, status)
    with pytest.raises(ValueError, match="final_post_not_editable"):
        await repo.get_final_post_edit_snapshot(selected)


@pytest.mark.asyncio
async def test_missing_source_does_not_allow_manual_text_to_bypass_grounding(editorial):
    repo, selected, _, _, _ = editorial
    await repo.update_item_content(selected, "https://example.test/only-url")
    with pytest.raises(ValueError, match="final_post_source_unavailable"):
        await repo.get_final_post_edit_snapshot(selected)


@pytest.mark.asyncio
async def test_explicit_generation_after_edit_clears_manual_marker(editorial):
    from telegram_bot.views import save_owner_review_comment

    repo, selected, _, _, _ = editorial
    token = (await repo.get_final_post_edit_snapshot(selected))["token"]
    await repo.save_manual_final_post(selected, "Текст вручную", expected_token=token)
    await save_owner_review_comment(
        repo, selected, "Новый комментарий", user_id=1001, username=None
    )
    nlp = await repo.get_nlp_results(selected)
    assert not nlp["extra"].get("owner_manual_final_text")
    assert "Новый комментарий" in nlp["merged_text"]
