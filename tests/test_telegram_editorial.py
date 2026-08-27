from utils.telegram_editorial import analyze_telegram_post, format_telegram_editorial_html, hints_from_item


def test_news_band_and_lead_ok():
    hints = analyze_telegram_post(
        title="Кубок России",
        body="1 июля в Рязани стартует этап. Заявки до пятницы. Расписание на сайте федерации.",
    )
    assert hints.band in {"short", "news"}
    assert hints.lead_ok is True
    html_block = format_telegram_editorial_html(hints)
    assert "Формат Telegram" in html_block
    assert "Знаков:" in html_block


def test_water_lead_and_long_warn():
    water_open = "Как известно, в наше время целесообразно осуществить комплекс мер. "
    body = (water_open + "Текст. ") * 80
    hints = analyze_telegram_post(title="Новость", body=body)
    assert hints.over_warn is True
    assert hints.lead_ok is False
    assert "в рамках" in hints.water_hits or "целесообразно" in hints.water_hits or "осуществить" in hints.water_hits


def test_hints_from_item_uses_summary_and_notes():
    item = {"title": "Заголовок", "content": "длинный оригинал " * 40, "images": ""}
    nlp = {"summary": "Короткое саммари новости.", "author_notes": "Мой комментарий."}
    hints = hints_from_item(item, nlp)
    assert "Короткое саммари" in (hints.lead + str(hints.chars))
    assert hints.chars < len(item["content"]) + 50
