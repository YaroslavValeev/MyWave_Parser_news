"""Inline и reply-клавиатуры редактора."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# Тексты кнопок главного меню (совпадают с F.text в router)
MENU_PARSE = "📥 Собрать новости"
MENU_REVIEW = "📋 Ревью"
MENU_PUBLISH = "📤 Опубликовать"
MENU_PROBE = "🔎 Проверить источник"
MENU_STATS = "📊 Статус"
MENU_HELP = "ℹ️ Помощь"


def main_menu_reply_keyboard():
    """Нижнее меню как у «полноценного» бота парсера."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=MENU_PARSE)
    builder.button(text=MENU_REVIEW)
    builder.button(text=MENU_PUBLISH)
    builder.button(text=MENU_PROBE)
    builder.button(text=MENU_STATS)
    builder.button(text=MENU_HELP)
    builder.adjust(2, 2, 2)
    return builder.as_markup(resize_keyboard=True)


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
        text="⏸ Отложить",
        callback_data=ReviewAction(action="defer", item_id=item_id).pack(),
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
    builder.button(
        text="🖼 Добавить/заменить обложку",
        callback_data=ReviewAction(action="add_cover", item_id=item_id).pack(),
    )
    builder.button(
        text="🔁 Retry media",
        callback_data=ReviewAction(action="retry_media", item_id=item_id).pack(),
    )
    if include_publish:
        builder.button(
            text="📢 Публиковать сейчас",
            callback_data=ReviewAction(action="publish_now", item_id=item_id).pack(),
        )
        builder.adjust(2, 2, 2, 1, 1, 1)
    else:
        builder.adjust(2, 2, 2, 1, 1)
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


def owner_review_card_markup(
    item_id: int,
    *,
    include_publish: bool = True,
    has_cover: bool = False,
) -> InlineKeyboardMarkup:
    """Одна inline-клавиатура карточки ревью: без дублей (текст/голос, одна публикация, переписка по комментарию)."""

    b = InlineKeyboardBuilder()
    b.button(
        text="✅ Одобрить",
        callback_data=ReviewAction(action="approve", item_id=item_id).pack(),
    )
    b.button(
        text="🗑 Отклонить",
        callback_data=ReviewAction(action="discard", item_id=item_id).pack(),
    )
    b.adjust(2)
    b.button(
        text="⏸ Отложить",
        callback_data=ReviewAction(action="defer", item_id=item_id).pack(),
    )
    b.adjust(1)
    b.button(
        text="✍️ Комментарий текстом",
        callback_data=PersonalReviewAction(action="text", item_id=item_id).pack(),
    )
    b.button(
        text="🎤 Комментарий голосом",
        callback_data=PersonalReviewAction(action="voice", item_id=item_id).pack(),
    )
    b.adjust(2)
    b.button(
        text="🔗 Источник",
        callback_data=ReviewAction(action="open_source", item_id=item_id).pack(),
    )
    b.button(
        text="🧠 Перегенерировать NLP",
        callback_data=ReviewAction(action="retry_nlp", item_id=item_id).pack(),
    )
    b.adjust(2)
    b.button(
        text="🖼 Добавить/заменить обложку",
        callback_data=ReviewAction(action="add_cover", item_id=item_id).pack(),
    )
    b.button(
        text="🔁 Retry media",
        callback_data=ReviewAction(action="retry_media", item_id=item_id).pack(),
    )
    b.adjust(2)
    if include_publish:
        b.button(
            text="📢 В очередь публикации",
            callback_data=ReviewAction(action="publish_now", item_id=item_id).pack(),
        )
        b.adjust(1)
    b.button(
        text="✏️ Переписать саммари по комментарию",
        callback_data=AuthorDecisionAction(action="rewrite", item_id=item_id).pack(),
    )
    b.adjust(1)
    return b.as_markup()


def combined_owner_review_markup(item_id: int, *, include_publish: bool = True) -> InlineKeyboardMarkup:
    """Обратная совместимость: то же, что owner_review_card_markup."""

    return owner_review_card_markup(item_id, include_publish=include_publish)


__all__ = [
    "AuthorDecisionAction",
    "MENU_HELP",
    "MENU_PARSE",
    "MENU_PROBE",
    "MENU_PUBLISH",
    "MENU_REVIEW",
    "MENU_STATS",
    "PersonalReviewAction",
    "ReviewAction",
    "author_decision_keyboard",
    "combined_owner_review_markup",
    "owner_review_card_markup",
    "main_menu_reply_keyboard",
    "personal_review_keyboard",
    "review_keyboard",
]
