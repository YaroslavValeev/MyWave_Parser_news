"""
Парсинг website-источников для manual_collect / scheduler.
Обёртка над WebsiteParser из utils.import_asyncio.
"""
from __future__ import annotations

from typing import Any, List

from config.settings import config


async def parse_website_async(source: Any, filter_keywords: list | None = None) -> List[dict]:
    """
    Асинхронный парсинг страницы сайта.
    filter_keywords зарезервирован под совместимость с RSS (пока не используется).
    """
    from utils.import_asyncio import WebsiteParser

    limit = getattr(config, "MAX_MESSAGES", None) or 50
    parser = WebsiteParser(limit=int(limit))
    return await parser.parse(source, date_from=None, date_to=None)
