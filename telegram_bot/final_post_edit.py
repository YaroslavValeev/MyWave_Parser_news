"""Manual final-post replacement in Owner review; no generation or publication."""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from services.publication import PublicationService
from storage.repository import AsyncNewsRepository
from telegram_bot.access import is_bot_operator
from telegram_bot.keyboards import AuthorDecisionAction, owner_review_card_markup

LOGGER = logging.getLogger(__name__)


class FinalPostEditForm(StatesGroup):
    waiting_text = State()


def register_final_post_edit(router: Router, repo: AsyncNewsRepository) -> None:
    # Register before the generic AuthorDecisionAction and text handlers.
    @router.callback_query(AuthorDecisionAction.filter(F.action == "edit_final"))
    async def begin_final_post_edit(
        query: CallbackQuery, callback_data: AuthorDecisionAction, state: FSMContext
    ):
        uid = query.from_user.id if query.from_user else None
        if not is_bot_operator(uid):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if not query.message:
            await query.answer("Откройте карточку материала снова.", show_alert=True)
            return
        try:
            snapshot = await repo.get_final_post_edit_snapshot(callback_data.item_id)
        except aiosqlite.Error:
            LOGGER.exception(
                "manual final post read failed item_id=%s", callback_data.item_id
            )
            await query.answer(
                "Не удалось открыть текст. Попробуйте ещё раз.", show_alert=True
            )
            return
        except ValueError as exc:
            reason = (
                "Сначала загрузите текст источника и обновите саммари."
                if str(exc) == "final_post_source_unavailable"
                else "Редактировать можно материал в ревью или отложенный до публикации."
            )
            await query.answer(reason, show_alert=True)
            return
        nlp = snapshot["nlp"]
        current_text = str(nlp.get("merged_text") or nlp.get("summary") or "").strip()
        await state.clear()
        await state.set_state(FinalPostEditForm.waiting_text)
        await state.update_data(
            final_edit_item_id=callback_data.item_id, final_edit_token=snapshot["token"]
        )
        await query.answer()
        await query.message.answer(
            "Скопируйте текст ниже, отредактируйте и пришлите полный вариант одним сообщением.\n"
            "До 3500 символов, обычный текст; абзацы сохраняются.\n"
            "Бот сохранит вашу версию без переписывания моделью. "
            "Ссылки внизу публикации добавляются автоматически.\n"
            "/cancel — отмена.",
            parse_mode=None,
        )
        # Never truncate the draft shown for copying.
        for offset in range(0, len(current_text), 1800):
            await query.message.answer(
                current_text[offset : offset + 1800], parse_mode=None
            )

    @router.message(FinalPostEditForm.waiting_text, F.text, ~F.text.startswith("/"))
    async def receive_final_post_edit(message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else None
        if not is_bot_operator(uid):
            await state.clear()
            return
        text = (message.text or "").strip()
        if text.lower() == "отмена":
            await state.clear()
            await message.answer("Редактирование отменено.")
            return
        if not text or len(text.encode("utf-16-le")) // 2 > 3500:
            await message.answer(
                "Пришлите непустой полный текст до 3500 символов или /cancel."
            )
            return
        data = await state.get_data()
        item_id, token = data.get("final_edit_item_id"), data.get("final_edit_token")
        if not item_id or not token:
            await state.clear()
            await message.answer(
                "Сессия устарела. Откройте карточку и начните редактирование снова."
            )
            return
        try:
            snapshot = await repo.get_final_post_edit_snapshot(int(item_id))
            preview = {
                **snapshot["nlp"],
                "merged_text": text,
                "extra": {"owner_manual_final_text": True},
            }
            if PublicationService._telegram_caption_too_long(
                PublicationService._build_caption(snapshot["item"], preview)
            ):
                await message.answer(
                    "Текст со ссылками слишком длинный для Telegram. Сократите его или /cancel."
                )
                return
            await repo.save_manual_final_post(
                int(item_id), text, expected_token=token, user_id=uid
            )
        except ValueError:
            await state.clear()
            await message.answer(
                "Материал изменился или больше недоступен для редактирования. "
                "Ваш текст не сохранён. Откройте карточку и начните снова."
            )
            return
        except aiosqlite.Error:
            LOGGER.exception("manual final post save failed item_id=%s", item_id)
            await message.answer(
                "Не удалось сохранить текст. Попробуйте ещё раз или /cancel."
            )
            return
        await state.clear()
        from telegram_bot.views import build_review_card_html

        # Render saved data directly: the general card path may translate sources.
        item = await repo.get_item(int(item_id))
        nlp = await repo.get_nlp_results(int(item_id))
        await message.answer(
            build_review_card_html(
                item,
                nlp,
                banner="Финальный текст сохранён. Проверьте карточку перед публикацией.",
            ),
            parse_mode="HTML",
            reply_markup=owner_review_card_markup(int(item_id)),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(FinalPostEditForm.waiting_text, ~F.text)
    async def final_post_edit_needs_text(message: Message):
        if is_bot_operator(message.from_user.id if message.from_user else None):
            await message.answer(
                "Для редактирования пришлите полный текст сообщением или /cancel."
            )
