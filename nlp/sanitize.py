"""Утилиты очистки входного текста перед подачей в модели."""
from __future__ import annotations

import html
import importlib
import re

_bs4_spec = importlib.util.find_spec("bs4")
if _bs4_spec is not None:
    _bs4_module = importlib.import_module("bs4")
    BeautifulSoup = getattr(_bs4_module, "BeautifulSoup")
else:
    BeautifulSoup = None

_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_text(text: str | None) -> str:
    """Очистить HTML/Markdown и нормализовать пробелы.

    Возвращает строку без тэгов и управляющих символов, пригодную для передачи в
    модели NLP. Пустые строки приводятся к "".
    """

    if not text:
        return ""

    extracted: str
    if BeautifulSoup is not None:
        soup = BeautifulSoup(text, "html.parser")
        extracted = soup.get_text(separator=" ", strip=True)
    else:
        extracted = re.sub(r"<[^>]+>", " ", text)
    normalized = _WHITESPACE_RE.sub(" ", html.unescape(extracted or "")).strip()
    return normalized


__all__ = ["sanitize_text"]
