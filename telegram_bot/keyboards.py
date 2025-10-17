"""Inline keyboards for редакторского бота."""
from __future__ import annotations

from aiogram.utils.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder


class ReviewAction(CallbackData, prefix="rev"):
    """Payload для inline-кнопок ревью."""

    action: str
    item_id: int


def review_keyboard(item_id: int, *, include_publish: bool = False):
    """Собрать клавиатуру для карточки ревью."""

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Одобрить",
        callback_data=ReviewAction(action="approve", item_id=item_id).pack(),
    )
    builder.button(
        text="🗑 Отклонить",
        callback_data=ReviewAction(action="discard", item_id=item_id).pack(),
    )
    builder.button(
        text="💬 Комментарий",
        callback_data=ReviewAction(action="comment", item_id=item_id).pack(),
    )
    builder.button(
        text="🔗 Источник",
        callback_data=ReviewAction(action="open_source", item_id=item_id).pack(),
    )
    builder.button(
        text="🧠 Перегенерировать",
        callback_data=ReviewAction(action="retry_nlp", item_id=item_id).pack(),
    )
    if include_publish:
        builder.button(
            text="📢 Публиковать сейчас",
            callback_data=ReviewAction(action="publish_now", item_id=item_id).pack(),
        )
    builder.adjust(2, 2, 2)
    return builder.as_markup()


class PersonalReviewAction(CallbackData, prefix="prv"):
    """Payload для кнопок персонального ревью."""

    action: str
    item_id: int


class AuthorDecisionAction(CallbackData, prefix="auth"):
    """Payload для финальных решений автора."""

    action: str
    item_id: int


def personal_review_keyboard(item_id: int):
    """Клавиатура для владельца с вариантами обратной связи."""

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✍️ Написать комментарий",
        callback_data=PersonalReviewAction(action="text", item_id=item_id).pack(),
    )
    builder.button(
        text="🎤 Голосовой комментарий",
        callback_data=PersonalReviewAction(action="voice", item_id=item_id).pack(),
    )
    builder.button(
        text="🔁 Перегенерировать саммари",
        callback_data=PersonalReviewAction(action="regenerate", item_id=item_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def author_decision_keyboard(item_id: int):
    """Клавиатура для финального решения автора."""

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Опубликовать",
        callback_data=AuthorDecisionAction(action="publish", item_id=item_id).pack(),
    )
    builder.button(
        text="🗑 Пропустить",
        callback_data=AuthorDecisionAction(action="discard", item_id=item_id).pack(),
    )
    builder.button(
        text="✏️ Переписать",
        callback_data=AuthorDecisionAction(action="rewrite", item_id=item_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


__all__ = [
    "ReviewAction",
    "PersonalReviewAction",
    "AuthorDecisionAction",
    "review_keyboard",
    "personal_review_keyboard",
    "author_decision_keyboard",
]
