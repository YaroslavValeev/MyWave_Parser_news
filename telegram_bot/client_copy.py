"""Тексты для клиентских сообщений бота (HTML, parse_mode=HTML)."""
from __future__ import annotations

import html

from config.settings import config

_DEFAULT_YANDEX_REVIEW_URL = (
    "https://yandex.ru/maps/org/mywave/90003306477/reviews/"
    "?from=tableau_yabro&ll=36.202793%2C55.698531&tab=reviews&z=9.41"
)


def yandex_review_url() -> str:
    """URL страницы отзывов MyWave на Яндекс.Картах."""
    return (
        getattr(config, "YANDEX_REVIEW_URL", None)
        or _DEFAULT_YANDEX_REVIEW_URL
    ).strip()


def yandex_review_request_html() -> str:
    """Просьба оставить отзыв со ссылкой на слове «отзыв»."""
    url = yandex_review_url()
    if not url:
        return ""
    safe_url = html.escape(url, quote=True)
    return (
        "\n\nЕсли вам всё понравилось, оставьте "
        f'<a href="{safe_url}">отзыв</a> на Яндекс.Картах.'
    )


def training_media_request_html() -> str:
    """Финальное сообщение клиенту после тренировки: фото/видео + просьба об отзыве."""
    return (
        "Здравствуйте! Поделитесь фото/видео с тренировки."
        f"{yandex_review_request_html()}"
    )


__all__ = [
    "training_media_request_html",
    "yandex_review_request_html",
    "yandex_review_url",
]
