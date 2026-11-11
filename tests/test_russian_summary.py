from utils.russian_summary import ensure_russian_summary, has_cyrillic, is_probably_non_russian


def test_english_summary_is_replaced_with_russian_fallback():
    item = {
        "title": "Wakeboarding, Wakeboard Gear, Videos, Tips, Photos | Wakeboarding Mag",
        "content": "Wakeboarding Magazine covers the latest in wakeboarding gear, videos, tips, photos, boats, news, and so much more.",
    }

    summary = ensure_russian_summary(
        "Wakeboarding Magazine covers the latest in wakeboarding gear, videos, tips, photos, boats, news, and so much more.",
        item=item,
        source_text=item["content"],
        lang="ru",
    )

    assert has_cyrillic(summary)
    assert "Wakeboarding Magazine covers" not in summary
    assert "вейкбординге" in summary


def test_generic_russian_fallback_is_refined_by_article_title():
    item = {
        "title": "We Test: Betty x Tri 2 Headphones",
        "content": (
            "During our tests, the author found she could hear her surroundings and enjoy music "
            "while using these headphones. They are waterproof and designed for sports."
        ),
    }

    summary = ensure_russian_summary(
        "Материал о катерах и индустрии водного спорта.",
        item=item,
        source_text=item["content"],
        lang="ru",
    )

    assert "катерах и индустрии водного спорта" not in summary
    assert "Betty x Tri 2 Headphones" in summary
    assert "Обзор" in summary


def test_russian_summary_passes_through():
    summary = "Материал о соревнованиях по вейкбордингу и новых правилах сезона."

    assert ensure_russian_summary(summary, lang="ru") == summary
    assert not is_probably_non_russian(summary)

