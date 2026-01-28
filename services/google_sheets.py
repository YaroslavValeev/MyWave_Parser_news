"""
DEPRECATED (P0):
Ранее здесь была позиционная запись "на 14–15 полей", что ломает фактическую структуру raw_feed.

Каноничная интеграция с Google Sheets:
- `utils/import asyncio.py` (через shim `utils/import_asyncio.py`)
- header-based запись по заголовкам листа
- обязательное заполнение `row_number` при вставке (fail-fast)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)


class GoogleSheets:
    """
    Совместимый враппер для старых вызовов.
    Для нового кода используйте напрямую `utils.import_asyncio.save_to_sheet/update_sheet_row`.
    """

    def append_news_batch(self, news_items: Iterable[Any]) -> None:
        """
        Добавляет новости в raw_feed через каноничную header-based запись.
        В sync-контексте запускает небольшой async-runner.
        """

        async def _run():
            from utils.import_asyncio import init_google_sheets, save_to_sheet

            doc = await init_google_sheets()
            if not doc:
                return

            rows: List[Dict[str, Any]] = []
            for n in news_items:
                # Поддержка dict / pydantic / объектов с атрибутами
                if isinstance(n, dict):
                    rows.append(n)
                else:
                    rows.append(getattr(n, "model_dump", lambda: n.__dict__)())

            await save_to_sheet(doc, "raw_feed", rows)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_run())
            return

        # Если уже внутри event loop — fail-fast: используйте async API напрямую
        raise RuntimeError(
            "GoogleSheets.append_news_batch вызван внутри running event loop. "
            "Используйте async API: await utils.import_asyncio.save_to_sheet(...)"
        )
