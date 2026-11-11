"""Извлечение обложки из RSS entry и (при необходимости) со страницы статьи."""
from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

_SKIP_IMG_HINTS = (
    "logo",
    "sprite",
    "icon",
    "avatar",
    "1x1",
    "pixel",
    "tracking",
    "badge",
    "button",
    "emoji",
    "gravatar",
    "s.w.org",
    "wp-includes",
    "favicon",
)


def _is_plausible_image_url(url: str) -> bool:
    text = (url or "").strip()
    if not text or text.startswith("data:"):
        return False
    low = text.lower()
    if any(hint in low for hint in _SKIP_IMG_HINTS):
        return False
    parsed = urlparse(text)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    path = (parsed.path or "").lower()
    # Крошечные служебные png (emoji 72x72 и т.п.)
    if "/72x72/" in path or "/16x16/" in path or "/32x32/" in path:
        return False
    return True


def extract_images_from_html(html: str, *, base_url: str = "") -> list[str]:
    """Картинки из HTML summary/content RSS."""
    if not (html or "").strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()
    for selector, attr in (
        ("meta[property='og:image'][content]", "content"),
        ("meta[name='twitter:image'][content]", "content"),
        ("img[src]", "src"),
        ("img[data-src]", "data-src"),
        ("img[data-lazy-src]", "data-lazy-src"),
        ("img[data-original]", "data-original"),
    ):
        for node in soup.select(selector):
            raw = str(node.get(attr) or "").strip()
            if not raw:
                continue
            absolute = urljoin(base_url, raw) if base_url else raw
            if not _is_plausible_image_url(absolute):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            found.append(absolute)
    return found


def extract_images_from_rss_entry(entry, *, base_url: str = "") -> list[str]:
    """media_content / enclosure / HTML content — без сетевых запросов."""
    images: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        text = (url or "").strip()
        if not text or not _is_plausible_image_url(text) or text in seen:
            return
        seen.add(text)
        images.append(text)

    for media in entry.get("media_content", []) or []:
        if not isinstance(media, dict):
            continue
        url = str(media.get("url") or "").strip()
        mtype = str(media.get("type") or "").strip().lower()
        medium = str(media.get("medium") or "").strip().lower()
        if mtype.startswith("image") or medium == "image" or not mtype:
            _add(url)

    for media in entry.get("media_thumbnail", []) or []:
        if isinstance(media, dict):
            _add(str(media.get("url") or ""))

    for enc in entry.get("enclosures", []) or []:
        if not isinstance(enc, dict):
            continue
        url = str(enc.get("href") or enc.get("url") or "").strip()
        mtype = str(enc.get("type") or "").strip().lower()
        if mtype.startswith("image") or url.lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif")
        ):
            _add(url)

    content_blocks = entry.get("content") or []
    html_chunks: list[str] = []
    if content_blocks and isinstance(content_blocks[0], dict):
        html_chunks.append(str(content_blocks[0].get("value") or ""))
    html_chunks.append(str(entry.get("summary") or ""))
    link = str(entry.get("link") or base_url or "").strip()
    for chunk in html_chunks:
        for url in extract_images_from_html(chunk, base_url=link):
            _add(url)

    return images


def fetch_og_image_from_article(article_url: str, *, timeout: float = 15.0) -> str:
    """Догрузка og:image со страницы статьи (для лент без enclosure, напр. wakeboardingmag)."""
    url = (article_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        from utils.safe_http import safe_get

        response = safe_get(
            url,
            timeout=timeout,
            headers={"User-Agent": "MyWaveParserBot/1.0 (+https://mywavewake.ru)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        html = response.text or ""
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("og:image fetch failed url=%s err=%s", url, type(exc).__name__)
        return ""

    images = extract_images_from_html(html, base_url=url)
    return images[0] if images else ""


def collect_rss_entry_images(
    entry,
    *,
    fetch_article_og: bool = True,
    timeout: float = 15.0,
) -> list[str]:
    """Картинки entry: og:image со страницы (приоритет) + media/HTML из ленты."""
    link = str(entry.get("link") or "").strip()
    from_feed = extract_images_from_rss_entry(entry, base_url=link)
    images: list[str] = []
    seen: set[str] = set()

    def _push(url: str) -> None:
        text = (url or "").strip()
        if not text or text in seen or not _is_plausible_image_url(text):
            return
        seen.add(text)
        images.append(text)

    # Hero с страницы статьи надёжнее встроенных «Specs»/emoji из content.
    if fetch_article_og and link:
        og = fetch_og_image_from_article(link, timeout=timeout)
        _push(og)
    for url in from_feed:
        _push(url)
    return images


__all__ = [
    "collect_rss_entry_images",
    "extract_images_from_html",
    "extract_images_from_rss_entry",
    "fetch_og_image_from_article",
]
