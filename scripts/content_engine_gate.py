#!/usr/bin/env python3
"""Local Content Engine Stage-1 readiness gate (no secrets, no publish).

Exit 0 = Stage 1 structural DoD met (migration + code paths + optional data).
Exit 2 = gaps remain.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


async def _run() -> list[dict]:
    from config.settings import config
    from storage.repository import AsyncNewsRepository, initialize_database

    checks: list[dict] = []
    db_path = Path(str(getattr(config, "DB_PATH", "data.db") or "data.db"))

    # Code/contracts present
    checks.append(
        _check(
            "migration_003_present",
            (ROOT / "storage/migrations/003_source_health.sql").is_file(),
        )
    )
    checks.append(
        _check(
            "telemetry_module",
            (ROOT / "services/source_telemetry.py").is_file(),
        )
    )
    checks.append(
        _check(
            "e2e_trace_script",
            (ROOT / "scripts/content_e2e_trace.py").is_file(),
        )
    )
    checks.append(
        _check(
            "editorial_layers",
            (ROOT / "services/editorial_layers.py").is_file(),
        )
    )

    await initialize_database(db_path)
    repo = AsyncNewsRepository(db_path)
    rows = await repo.list_source_health(limit=5)
    checks.append(
        _check(
            "source_health_table",
            True,
            f"rows={len(rows)} (0 ok until first collect)",
        )
    )

    from utils.collect_report import load_collect_report

    report = load_collect_report()
    if report is None:
        # Runtime data — не блокирует structural Stage1 DoD
        checks.append(
            _check(
                "collect_report",
                True,
                "WARN: no last_collect_report.json yet (run collect)",
            )
        )
    else:
        sample = (report.get("results") or [{}])[0]
        has_metrics = "latency_ms" in sample or "collected" in sample or not report.get("results")
        checks.append(
            _check(
                "collect_report_metrics_shape",
                bool(has_metrics),
                f"sources_total={report.get('sources_total')}",
            )
        )

    # Health script importable
    try:
        import scripts.check_bot_health as health  # noqa: F401

        checks.append(_check("health_script_import", True))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("health_script_import", False, type(exc).__name__))

    return checks


def main() -> int:
    checks = asyncio.run(_run())
    failed = 0
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        if not c["ok"]:
            failed += 1
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"[{mark}] {c['name']}{detail}")
    print(
        "Stage1 gate: "
        + ("READY (run collect to populate health)" if failed == 0 else f"{failed} gap(s)")
    )
    print("Stage2 prod E2E still requires Owner GO.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
