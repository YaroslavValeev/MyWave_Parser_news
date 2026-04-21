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

    def __init__(self, credentials_path: str | None = None, sheet_id: str | None = None) -> None:
        # Сохраняем параметры только для совместимости со старым API.
        # Каноничная инициализация берется из config/settings через init_google_sheets().
        self._credentials_path = credentials_path
        self._sheet_id = sheet_id

    def _run_sync(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError(
            "GoogleSheets sync-метод вызван внутри running event loop. "
            "Используйте async API из utils.import_asyncio."
        )

    async def _collect_existing_column(self, column_name: str) -> set[str]:
        from utils.import_asyncio import get_worksheet, init_google_sheets

        doc = await init_google_sheets()
        if not doc:
            return set()

        ws = get_worksheet(doc, "raw_feed")
        if ws is None:
            return set()

        values = ws.get_all_values()
        if not values:
            return set()

        header = values[0]
        try:
            column_index = header.index(column_name)
        except ValueError:
            return set()

        result: set[str] = set()
        for row in values[1:]:
            if len(row) > column_index and row[column_index]:
                result.add(row[column_index])
        return result

    def get_existing_ids(self) -> set[str]:
        return self._run_sync(self._collect_existing_column("id"))

    def get_existing_checksums(self) -> set[str]:
        return self._run_sync(self._collect_existing_column("checksum"))

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
