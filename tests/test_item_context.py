from utils.item_context import (
    derive_item_title,
    is_title_only_summary_fallback,
    missing_text_context_summary,
)


def test_derive_item_title_prefers_content_for_telegram_items():
    item = {
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "Как насчет розыгрыша?\nНа кону вакстрак кросфаер.",
        "link": "https://t.me/talktofish/352",
    }
    assert derive_item_title(item) == "Как насчет розыгрыша?"


def test_derive_item_title_uses_safe_fallback_for_empty_telegram_items():
    item = {
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "",
        "link": "https://t.me/talktofish/347",
    }
    assert derive_item_title(item) == "Пост из ДИАЛОГИ О РЫБАЛКЕ #347"


def test_is_title_only_summary_fallback_detects_old_nlp_record():
    item = {
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "",
        "link": "https://t.me/talktofish/347",
    }
    nlp = {
        "summary": "Кристина Колесникова — российская певица...",
        "extra": {"sanitized_text": "Cristina Kolesnikova"},
    }
    assert is_title_only_summary_fallback(item, nlp) is True


def test_missing_text_context_summary_is_manual_review_placeholder():
    item = {
        "source": "ДИАЛОГИ О РЫБАЛКЕ",
        "title": "Cristina Kolesnikova",
        "content": "",
        "link": "https://t.me/talktofish/347",
    }
    text = missing_text_context_summary(item)
    assert "нет текстового контента" in text
    assert "Пост из ДИАЛОГИ О РЫБАЛКЕ #347" in text


def test_derive_item_title_ignores_placeholder_title_when_content_exists():
    item = {
        "source": "Unleashed Wake Magazine",
        "title": "(без заголовка)",
        "content": "Wake cable reaches the Olympics shortlist.",
        "link": "https://example.com/post",
    }
    assert derive_item_title(item) == "Wake cable reaches the Olympics shortlist."
