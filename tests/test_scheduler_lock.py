"""Проверка единственного активного parse_all_sources."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from core.scheduler import (
    ParseAllSourcesBusyError,
    ParseAllSummary,
    parse_all_sources,
    parse_all_sources_busy,
)


class TestSchedulerLock(unittest.IsolatedAsyncioTestCase):
    async def test_second_parse_raises_busy_while_first_holds_lock(self) -> None:
        gate = asyncio.Event()
        release = asyncio.Event()

        async def slow_impl() -> ParseAllSummary:
            gate.set()
            await release.wait()
            return ParseAllSummary(
                news_saved=0,
                contacts_saved=0,
                elapsed_seconds=0.0,
                sources_total=0,
                sources_failed=0,
            )

        with patch("core.scheduler._parse_all_sources_impl", new=slow_impl):
            t1 = asyncio.create_task(parse_all_sources())
            await asyncio.wait_for(gate.wait(), timeout=2.0)
            self.assertTrue(parse_all_sources_busy())

            with self.assertRaises(ParseAllSourcesBusyError):
                await parse_all_sources()

            release.set()
            await t1

    async def test_wait_if_busy_waits_for_lock(self) -> None:
        started = asyncio.Event()
        finish_first = asyncio.Event()

        async def slow_impl() -> ParseAllSummary:
            started.set()
            await finish_first.wait()
            return ParseAllSummary(0, 0, 0.0, 1, 0)

        with patch("core.scheduler._parse_all_sources_impl", new=slow_impl):
            t1 = asyncio.create_task(parse_all_sources(wait_if_busy=True))
            await asyncio.wait_for(started.wait(), timeout=2.0)
            t2 = asyncio.create_task(parse_all_sources(wait_if_busy=True))
            await asyncio.sleep(0.05)
            self.assertFalse(t2.done())
            finish_first.set()
            await t1
            await t2


if __name__ == "__main__":
    unittest.main()
