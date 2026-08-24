"""Per-source collect telemetry (Content Engine Stage 1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse


def source_key(source_type: str, source_url: str, source_name: str = "") -> str:
    """Stable key: type + normalized url (fallback to name)."""
    st = (source_type or "unknown").strip().lower() or "unknown"
    url = (source_url or "").strip()
    name = (source_name or "").strip()
    if url:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").rstrip("/")
        norm = f"{host}{path}" if host else url.lower()
        return f"{st}|{norm}"
    return f"{st}|{name.lower() or 'unnamed'}"


@dataclass(slots=True)
class SourceTickMetrics:
    """One collect tick for a single source."""

    source_type: str
    source_name: str
    source_url: str
    ok: bool
    latency_ms: float
    collected: int = 0
    parsed: int = 0
    saved: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: int = 0
    error: str = ""

    @property
    def key(self) -> str:
        return source_key(self.source_type, self.source_url, self.source_name)

    def to_result_row(self) -> dict[str, Any]:
        return {
            "type": self.source_type,
            "name": self.source_name,
            "url": self.source_url,
            "ok": self.ok,
            "news_saved": self.saved,
            "collected": self.collected,
            "parsed": self.parsed,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "errors": self.errors,
            "latency_ms": round(self.latency_ms, 1),
            "error": (self.error or "")[:300],
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_source_pipeline(
    rows: list[Mapping[str, Any]],
    *,
    stale_hours: float,
    fail_streak: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Content-pipeline health from source_health rows (not process-alive)."""
    now = now or datetime.now(timezone.utc)
    stale: list[str] = []
    streak: list[str] = []
    ok_recent = 0
    for row in rows:
        name = str(row.get("source_name") or row.get("source_key") or "?")[:80]
        consec = int(row.get("consecutive_failures") or 0)
        if consec >= fail_streak:
            streak.append(name)
        last_ok = row.get("last_success_at")
        if last_ok:
            try:
                ts = datetime.fromisoformat(str(last_ok).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (now - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
                if age_h <= stale_hours:
                    ok_recent += 1
                elif consec > 0 or int(row.get("last_ok") or 0) == 0:
                    stale.append(name)
            except ValueError:
                stale.append(name)
        elif consec > 0:
            stale.append(name)
    total = len(rows)
    content_ok = total == 0 or (not streak and (ok_recent > 0 or total == 0))
    # If we have sources but none succeeded recently and some failing — not ok
    if total > 0 and ok_recent == 0 and (streak or any(int(r.get("errors_total") or 0) > 0 for r in rows)):
        content_ok = False
    if streak:
        content_ok = False
    return {
        "content_pipeline_ok": content_ok,
        "sources_tracked": total,
        "sources_ok_recent": ok_recent,
        "stale_sources": stale[:20],
        "fail_streak_sources": streak[:20],
        "stale_hours": stale_hours,
        "fail_streak_threshold": fail_streak,
    }


def format_source_health_html(
    rows: list[Mapping[str, Any]] | None,
    *,
    stale_hours: float = 36.0,
    fail_streak: int = 3,
    limit: int = 8,
) -> str:
    """HTML-блок telemetry источников для /stats и /report."""
    rows = list(rows or [])
    if not rows:
        return (
            "\n\n<b>Source health</b>\n"
            "Пока нет данных — выполните сбор новостей (миграция source_health)."
        )
    verdict = evaluate_source_pipeline(
        rows, stale_hours=stale_hours, fail_streak=fail_streak
    )
    status = "ok" if verdict["content_pipeline_ok"] else "degraded"
    lines = [
        "\n\n<b>Source health</b>",
        f"\nPipeline: <code>{status}</code> "
        f"(tracked {verdict['sources_tracked']}, "
        f"ok recent {verdict['sources_ok_recent']})",
    ]
    if verdict["fail_streak_sources"]:
        lines.append(
            "\nFail streak: " + ", ".join(verdict["fail_streak_sources"][:5])
        )
    lines.append("\n<b>По источникам (последний тик):</b>")
    for row in rows[: max(1, limit)]:
        name = str(row.get("source_name") or row.get("source_key") or "?")[:40]
        ok = "✓" if int(row.get("last_ok") or 0) else "✗"
        lat = row.get("last_latency_ms")
        lat_s = f"{float(lat):.0f}ms" if lat is not None else "—"
        lines.append(
            f"\n• {ok} {name}: "
            f"col={int(row.get('last_collected') or 0)} "
            f"dup={int(row.get('last_duplicates') or 0)} "
            f"err={int(row.get('last_errors') or 0)} "
            f"{lat_s} "
            f"streak={int(row.get('consecutive_failures') or 0)}"
        )
    return "".join(lines)


__all__ = [
    "SourceTickMetrics",
    "evaluate_source_pipeline",
    "format_source_health_html",
    "source_key",
    "utc_now_iso",
]
