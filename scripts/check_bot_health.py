#!/usr/bin/env python3
"""Prod smoke without printing secrets.

Exit 0 = process can import and DB is readable.
Content pipeline status is reported separately (Stage 1): process-alive ≠ Content Engine.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def main() -> int:
    from config.settings import config
    from services.source_telemetry import evaluate_source_pipeline
    from utils.collect_report import load_collect_report

    errors: list[str] = []
    token_set = bool(str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip())
    if not token_set:
        errors.append("TELEGRAM_BOT_TOKEN missing")

    try:
        import bot_aiogram  # noqa: F401
        from telegram_bot.router import create_router  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"import_failed:{type(exc).__name__}")

    db_path = Path(str(getattr(config, "DB_PATH", "data.db") or "data.db"))
    if db_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.execute("SELECT 1")
            conn.close()
        except sqlite3.Error:
            errors.append("sqlite_unreadable")
    else:
        errors.append("sqlite_missing")

    report = load_collect_report()
    collect_note = "no_collect_report"
    if report:
        total = int(report.get("sources_total") or 0)
        failed = int(report.get("sources_failed") or 0)
        collect_note = f"collect_ok={max(0, total - failed)}/{total}"

    content_note = "content_pipeline=unknown"
    try:
        from storage.repository import AsyncNewsRepository, initialize_database

        async def _load_health() -> list:
            if not db_path.is_file():
                return []
            await initialize_database(db_path)
            repo = AsyncNewsRepository(db_path)
            return await repo.list_source_health(limit=500)

        rows = asyncio.run(_load_health())
        stale_h = float(getattr(config, "SOURCE_HEALTH_STALE_HOURS", 36) or 36)
        streak = int(getattr(config, "SOURCE_HEALTH_FAIL_STREAK", 3) or 3)
        verdict = evaluate_source_pipeline(rows, stale_hours=stale_h, fail_streak=streak)
        content_note = (
            f"content_pipeline={'ok' if verdict['content_pipeline_ok'] else 'degraded'}"
            f" tracked={verdict['sources_tracked']}"
            f" ok_recent={verdict['sources_ok_recent']}"
            f" fail_streak={len(verdict['fail_streak_sources'])}"
        )
    except Exception as exc:  # noqa: BLE001
        content_note = f"content_pipeline=error:{type(exc).__name__}"

    openai_set = bool(str(getattr(config, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY") or "").strip())
    sheet_set = bool(str(getattr(config, "GOOGLE_SHEET_ID", "") or "").strip())
    print(
        "health "
        + ("ok" if not errors else "fail")
        + f" db={'yes' if db_path.is_file() else 'no'}"
        + f" openai={'set' if openai_set else 'missing'}"
        + f" sheets={'set' if sheet_set else 'missing'}"
        + f" {collect_note}"
        + f" {content_note}"
    )
    for item in errors:
        print(f"error:{item}")
    # Process health fails hard; content degradation is informational (exit still 0 if process ok).
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
