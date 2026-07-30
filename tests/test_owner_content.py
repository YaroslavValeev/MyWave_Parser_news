from utils.owner_content import build_fallback_merged_text, strip_author_meta_labels


def test_build_fallback_merged_text_integrates_notes_without_label():
    merged = build_fallback_merged_text(
        source_text="Brisbane 2032 wake cable olympic entry discussion.",
        author_notes="Скрестили пальцы и делаем всё возможное.",
        title="Brisbane 2032 wake cable",
    )

    assert "Мнение автора" not in merged
    assert "Brisbane 2032" in merged
    assert "Скрестили пальцы" in merged


def test_build_fallback_keeps_owner_notes_almost_raw():
    merged = build_fallback_merged_text(
        source_text="Краткое саммари новости.",
        author_notes="Мой комментарий!!!\n\nВторая строка.",
        title="",
    )
    assert "Краткое саммари" in merged
    assert "Мой комментарий!!!" in merged
    assert "Вторая строка." in merged


def test_strip_author_meta_labels():
    text = "Мнение автора\n\nТекст поста."
    cleaned = strip_author_meta_labels(text)
    assert cleaned == "Текст поста."
