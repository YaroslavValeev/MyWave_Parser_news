from utils.item_context import (
    derive_item_title,
    get_item_text_context,
    is_title_only_summary_fallback,
    missing_text_context_summary,
)


def test_link_without_article_text_is_not_nlp_context():
    item = {"content": "https://wakeflot.ru/news/1785"}
    assert get_item_text_context(item) == ""


def test_real_transcript_is_used_when_content_only_contains_a_link():
    item = {
        "content": "https://wakeflot.ru/news/1785",
        "transcript": "Консервация и зимнее хранение двигателей PCM.",
    }
    assert get_item_text_context(item) == item["transcript"]


def test_source_text_with_a_link_remains_available():
    content = "Консервация двигателей PCM: https://wakeflot.ru/news/1785"
    assert get_item_text_context({"content": content}) == content


def test_link_only_summary_is_untrusted_even_without_legacy_metadata():
    item = {"content": "https://wakeflot.ru/news/1785"}
    nlp = {"summary": "В Москве прошёл чемпионат России по вейкборду."}
    assert is_title_only_summary_fallback(item, nlp) is True


def test_missing_context_placeholder_cannot_become_a_publishable_summary():
    item = {"title": "Wakeflot", "content": ""}
    nlp = {
        "summary": "В записи нет текстового контента в базе.",
        "extra": {"source_context_missing": True},
    }
    assert is_title_only_summary_fallback(item, nlp) is True


def test_explicit_owner_rewrite_is_preserved_for_link_only_source():
    item = {"content": "https://wakeflot.ru/news/1785"}
    nlp = {
        "summary": "Подготовка двигателя PCM к зиме.",
        "merged_text": "Проверенный автором текст.",
        "extra": {"owner_rewritten": True, "source_context_missing": True},
    }
    assert is_title_only_summary_fallback(item, nlp) is False


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
