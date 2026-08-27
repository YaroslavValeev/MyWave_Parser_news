"""Последний прогон сбора: JSON рядом с БД. Без секретов и тел статей."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config.settings import config

LOGGER = logging.getLogger(__name__)


def collect_report_path() -> Path:
    db_path = Path(str(getattr(config, "DB_PATH", None) or "data.db"))
    parent = db_path.parent if db_path.parent.parts else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    return parent / "last_collect_report.json"


def save_collect_report(
    *,
    sources_total: int,
    sources_failed: int,
    news_saved: int,
    contacts_saved: int,
    elapsed_seconds: float,
    results: list[Mapping[str, Any]],
) -> Path | None:
    payload = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_total": int(sources_total),
        "sources_ok": int(sources_total) - int(sources_failed),
        "sources_failed": int(sources_failed),
        "news_saved": int(news_saved),
        "contacts_saved": int(contacts_saved),
        "elapsed_seconds": round(float(elapsed_seconds), 1),
        "results": [
            {
                "type": str(row.get("type") or ""),
                "name": str(row.get("name") or "")[:200],
                "url": str(row.get("url") or "")[:500],
                "ok": bool(row.get("ok")),
                "news_saved": int(row.get("news_saved") or 0),
                "collected": int(row.get("collected") or 0),
                "parsed": int(row.get("parsed") or 0),
                "duplicates": int(row.get("duplicates") or 0),
                "rejected": int(row.get("rejected") or 0),
                "errors": int(row.get("errors") or 0),
                "latency_ms": float(row.get("latency_ms") or 0.0),
                "error": str(row.get("error") or "")[:300],
            }
            for row in results
        ],
    }
    path = collect_report_path()
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.exception("cannot write collect report %s", path)
        return None
    return path


def load_collect_report() -> dict[str, Any] | None:
    path = collect_report_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("collect report unreadable: %s", path)
        return None
    return data if isinstance(data, dict) else None


def format_collect_report_html(report: Mapping[str, Any] | None) -> str:
    if not report:
        return (
            "\n\n<b>Последний сбор</b>\n"
            "Отчёта ещё нет — запустите «Собрать новости» или дождитесь расписания."
        )
    total = int(report.get("sources_total") or 0)
    failed = int(report.get("sources_failed") or 0)
    ok = int(report.get("sources_ok") or max(0, total - failed))
    rate = f"{(100.0 * ok / total):.0f}%" if total else "n/a"
    lines = [
        "\n\n<b>Последний сбор</b>",
        f"\nВремя (UTC): {str(report.get('finished_at') or '—')[:25]}",
        f"\nИсточники: ок {ok}/{total} ({rate}), ошибок {failed}",
        f"\nНовых в БД: {int(report.get('news_saved') or 0)}",
        f"\nДлительность: {report.get('elapsed_seconds') or 0} с",
    ]
    rows = [row for row in (report.get("results") or []) if isinstance(row, dict)]
    bad = [row for row in rows if not row.get("ok")]
    if bad:
        lines.append("\n<b>Ошибки:</b>")
        for row in bad[:8]:
            lines.append(
                f"\n• {row.get('name') or row.get('url')}: "
                f"{(row.get('error') or 'error')[:120]}"
            )
    # Краткая телеметрия по тику (Stage 1)
    with_metrics = [
        row
        for row in rows
        if int(row.get("collected") or 0)
        or int(row.get("duplicates") or 0)
        or float(row.get("latency_ms") or 0)
    ]
    if with_metrics:
        lines.append("\n<b>Telemetry (тик):</b>")
        for row in with_metrics[:6]:
            lines.append(
                f"\n• {row.get('name') or '?'}: "
                f"col={int(row.get('collected') or 0)} "
                f"dup={int(row.get('duplicates') or 0)} "
                f"{float(row.get('latency_ms') or 0):.0f}ms"
            )
    return "".join(lines)


__all__ = [
    "collect_report_path",
    "format_collect_report_html",
    "load_collect_report",
    "save_collect_report",
]
