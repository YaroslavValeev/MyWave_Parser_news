"""Тексты клиентских сообщений."""
from __future__ import annotations

from unittest.mock import patch

from telegram_bot.client_copy import (
    training_media_request_html,
    yandex_review_request_html,
    yandex_review_url,
)


def test_yandex_review_request_html_contains_link():
    html_block = yandex_review_request_html()
    assert "отзыв</a>" in html_block
    assert "yandex.ru/maps/org/mywave" in html_block
    assert 'href="' in html_block


def test_training_media_request_html_includes_review():
    text = training_media_request_html()
    assert text.startswith("Здравствуйте! Поделитесь фото/видео с тренировки.")
    assert "отзыв</a>" in text
    assert "Яндекс.Картах" in text


def test_yandex_review_request_html_empty_when_url_disabled():
    with patch("telegram_bot.client_copy.yandex_review_url", return_value=""):
        assert yandex_review_request_html() == ""
        assert (
            training_media_request_html()
            == "Здравствуйте! Поделитесь фото/видео с тренировки."
        )


def test_yandex_review_url_default():
    with patch("telegram_bot.client_copy.config") as cfg:
        cfg.YANDEX_REVIEW_URL = None
        url = yandex_review_url()
    assert "yandex.ru/maps/org/mywave" in url
