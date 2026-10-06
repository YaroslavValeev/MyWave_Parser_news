"""Bounded retrieval of one explicitly selected article from configured hosts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import socket
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from utils.safe_http import safe_get
from utils.source_context import (
    MAX_ARTICLE_TEXT,
    article_urls,
    normalized_article_url,
    source_input_hash,
)

MAX_HTML_BYTES = 524288
MIN_ARTICLE_TEXT = 200
SELECTORS = (
    "[itemprop='articleBody']",
    "#textContainer",
    ".entry-content",
    ".post-content",
    ".article-content",
    "article",
)


class ArticleFetchError(ValueError):
    """Safe predefined fetch refusal code; contains no URL/body/credentials."""


def _check_url(url, original, allowed_hosts):
    try:
        url = normalized_article_url(url)
        parsed, first = urlsplit(url), urlsplit(original)
    except ValueError as exc:
        raise ArticleFetchError("unsupported_url") from exc
    if parsed.hostname not in allowed_hosts:
        raise ArticleFetchError("host_not_allowed")
    if (
        parsed.hostname != first.hostname
        or parsed.path.rstrip("/") != first.path.rstrip("/")
        or parsed.query != first.query
    ):
        raise ArticleFetchError("redirect_source_changed")
    if first.scheme == "https" and parsed.scheme != "https":
        raise ArticleFetchError("https_downgrade")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except OSError as exc:
        raise ArticleFetchError("dns_failed") from exc
    if not addresses or any(
        not ipaddress.ip_address(info[4][0]).is_global for info in addresses
    ):
        raise ArticleFetchError("non_public_address")
    return url


def extract_article(html, final_url):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get("href"):
        target = urlsplit(urljoin(final_url, canonical["href"]))
        final = urlsplit(final_url)
        if (
            target.hostname != final.hostname
            or target.path.rstrip("/") != final.path.rstrip("/")
            or target.query != final.query
        ):
            raise ArticleFetchError("canonical_source_changed")
    headings = soup.select("h1")
    if len(headings) != 1:
        raise ArticleFetchError("ambiguous_article_heading")
    title = headings[0].get_text(" ", strip=True)[:300]
    if not title:
        raise ArticleFetchError("article_heading_missing")
    body = None
    for selector in SELECTORS:
        nodes = soup.select(selector)
        if len(nodes) == 1:
            body = nodes[0]
            break
    if body is None:
        raise ArticleFetchError("article_body_missing")
    # Optional structured data must identify this same page, not a related story.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except ValueError:
            continue
        if (
            isinstance(data, dict)
            and isinstance(data.get("@type"), str)
            and data["@type"] in {"NewsArticle", "Article", "BlogPosting"}
        ):
            entity = data.get("mainEntityOfPage") or data.get("url")
            entity = entity.get("@id") if isinstance(entity, dict) else entity
            if isinstance(entity, str):
                source, final = (
                    urlsplit(urljoin(final_url, entity)),
                    urlsplit(final_url),
                )
                if (
                    source.hostname != final.hostname
                    or source.path.rstrip("/") != final.path.rstrip("/")
                    or source.query != final.query
                ):
                    raise ArticleFetchError("structured_source_changed")
    for node in body.select(
        "script,style,nav,aside,footer,form,.related,.related-posts,.recommendations"
    ):
        node.decompose()
    text = body.get_text("\n", strip=True)
    if not MIN_ARTICLE_TEXT <= len(text) <= MAX_ARTICLE_TEXT:
        raise ArticleFetchError("article_text_length")
    return title, text


def fetch_article(item, allowed_hosts, *, timeout=15.0):
    urls = article_urls(item)
    if len(urls) != 1:
        raise ArticleFetchError("ambiguous_or_missing_url")
    original = urls[0]
    current = original
    deadline = time.monotonic() + timeout
    for _hop in range(4):
        current = _check_url(current, original, allowed_hosts)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArticleFetchError("fetch_timeout")
        response = None
        try:
            response = safe_get(
                current,
                timeout=min(remaining, 5),
                allow_redirects=False,
                stream=True,
                headers={"Accept": "text/html"},
            )
            if response.is_redirect:
                current = urljoin(current, response.headers.get("Location") or "")
                continue
            if response.status_code != 200:
                raise ArticleFetchError("http_error")
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise ArticleFetchError("not_html")
            raw = bytearray()
            for chunk in response.iter_content(16384):
                if time.monotonic() >= deadline:
                    raise ArticleFetchError("fetch_timeout")
                raw.extend(chunk)
                if len(raw) > MAX_HTML_BYTES:
                    raise ArticleFetchError("html_too_large")
            title, text = extract_article(bytes(raw), current)
            return {
                "version": 1,
                "requested_url": original,
                "final_url": current,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "title": title,
                "text": text,
                "input_sha256": source_input_hash(item),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "html_sha256": hashlib.sha256(raw).hexdigest(),
            }
        except ArticleFetchError:
            raise
        except Exception as exc:
            raise ArticleFetchError("fetch_failed") from exc
        finally:
            if response is not None:
                response.close()
    raise ArticleFetchError("redirect_limit")


async def retrieve_article(item, allowed_hosts):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fetch_article, item, allowed_hosts), timeout=16
        )
    except TimeoutError as exc:
        raise ArticleFetchError("fetch_timeout") from exc
