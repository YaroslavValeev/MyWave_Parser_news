"""Website collector compatibility module."""
from __future__ import annotations

import hashlib
from datetime import datetime

import requests
from bs4 import BeautifulSoup


def parse_website(source, filter_keywords=None):
    """Parse a website into raw-feed shaped items without external side effects."""

    response = requests.get(source.url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title = (soup.title.string if soup.title and soup.title.string else source.name).strip()
    text = soup.get_text(separator=" ", strip=True)
    checksum = hashlib.md5((title + source.url).encode("utf-8")).hexdigest()
    return [
        {
            "id": checksum,
            "source_type": "website",
            "source_name": source.name,
            "source_url": source.url,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "ingest_status": "raw",
            "raw_title": title,
            "raw_content": text,
            "raw_html": response.text,
            "raw_media": "[]",
            "raw_tags": ",".join(filter_keywords or []),
            "checksum": checksum,
            "parse_error": "",
            "debug_info": "",
        }
    ]
