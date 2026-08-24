#!/usr/bin/env python3
"""Prod smoke without printing secrets. Exit 0 = process can import and DB is readable."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def _present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def main() -> int:
    from config.settings import config
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

    openai_set = bool(str(getattr(config, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY") or "").strip())
    sheet_set = bool(str(getattr(config, "GOOGLE_SHEET_ID", "") or "").strip())
    print(
        "health "
        + ("ok" if not errors else "fail")
        + f" db={'yes' if db_path.is_file() else 'no'}"
        + f" openai={'set' if openai_set else 'missing'}"
        + f" sheets={'set' if sheet_set else 'missing'}"
        + f" {collect_note}"
    )
    for item in errors:
        print(f"error:{item}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
