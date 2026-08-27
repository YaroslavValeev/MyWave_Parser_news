from utils.web_editorial import analyze_web_body, format_web_editorial_html


def test_short_body_is_news_card_not_article():
    hints = analyze_web_body(title="Кубок", body="Короткий текст новости для витрины сайта.")
    assert hints.mode == "news_card"
    html_block = format_web_editorial_html(hints)
    assert "Формат сайт" in html_block
    assert "лонгрид" in html_block


def test_long_article_asks_for_h2():
    body = ("Абзац без заголовков. " * 40 + "\n\n") * 8
    hints = analyze_web_body(title="Статья", body=body)
    assert hints.mode == "article"
    assert hints.need_h2 is True
