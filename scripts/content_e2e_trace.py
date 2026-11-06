#!/usr/bin/env python3
"""Controlled Content E2E readiness check (Stage 2) — local / staging.

Does NOT publish. Traces one item_id through pipeline stages and prints a checklist.
Production run still requires Owner GO (see docs/integration/CONTROLLED_POST_E2E.md).

Usage:
  python scripts/content_e2e_trace.py --item-id 123
  python scripts/content_e2e_trace.py --latest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def _ok(flag: bool) -> str:
    return "PASS" if flag else "FAIL"


async def _trace(item_id: int | None, latest: bool) -> dict[str, Any]:
    from config.settings import config
    from services.editorial_layers import extract_editorial_layers, publication_allowed
    from services.semantic_dedup import maybe_attach_event_id, semantic_dedup_enabled
    from storage.repository import AsyncNewsRepository, initialize_database
    from utils.collect_report import load_collect_report

    db_path = Path(str(getattr(config, "DB_PATH", "data.db") or "data.db"))
    await initialize_database(db_path)
    repo = AsyncNewsRepository(db_path)

    item: dict[str, Any] | None = None
    if latest:
        rows = await repo.list_items(limit=1)
        item = rows[0] if rows else None
        item_id = int(item["id"]) if item else None
    elif item_id is not None:
        item = await repo.get_item(item_id)

    stages: list[dict[str, Any]] = []
    if not item or item_id is None:
        return {
            "item_id": item_id,
            "ok": False,
            "stages": [{"name": "item_exists", "ok": False, "detail": "not found"}],
        }

    nlp = await repo.get_nlp_results(item_id) or {}
    pub = await repo.get_publication_by_item(item_id)
    layers = extract_editorial_layers(item, nlp)
    allowed, deny = publication_allowed(layers)
    status = str(item.get("status") or "")
    extra = nlp.get("extra") if isinstance(nlp.get("extra"), dict) else {}
    event_id = extra.get("event_id") if isinstance(extra, dict) else None
    if not event_id and semantic_dedup_enabled():
        event_id = maybe_attach_event_id(item, nlp)

    health = await repo.list_source_health(limit=3)
    report = load_collect_report()

    stages.append(
        {
            "name": "source_raw",
            "ok": bool(item.get("source") and (item.get("title") or item.get("content"))),
            "detail": f"source={item.get('source')} checksum={str(item.get('checksum') or '')[:12]}",
        }
    )
    stages.append(
        {
            "name": "normalize_dedup_id",
            "ok": bool(item.get("checksum") and item_id),
            "detail": f"id={item_id} ( сквозной ID )",
        }
    )
    stages.append(
        {
            "name": "technical_dedup_constraint",
            "ok": bool(item.get("checksum")),
            "detail": "checksum UNIQUE in items",
        }
    )
    stages.append(
        {
            "name": "nlp",
            "ok": bool(str(nlp.get("summary") or "").strip()),
            "detail": f"summary_len={len(str(nlp.get('summary') or ''))}",
        }
    )
    stages.append(
        {
            "name": "editorial_layers",
            "ok": bool(layers.get("source_fact") or layers.get("auto_summary")),
            "detail": (
                f"fact={bool(layers.get('source_fact'))} "
                f"summary={bool(layers.get('auto_summary'))} "
                f"owner={bool(layers.get('owner_commentary'))}"
            ),
        }
    )
    stages.append(
        {
            "name": "owner_comment",
            "ok": allowed,
            "detail": "ok" if allowed else deny,
        }
    )
    media_ok = bool(item.get("images") or item.get("videos") or (extra or {}).get("cover"))
    stages.append(
        {
            "name": "media",
            "ok": media_ok,
            "detail": "has media refs" if media_ok else "no local media (may be text-only)",
        }
    )
    stages.append(
        {
            "name": "telegram_publish",
            "ok": pub is not None or status == "published",
            "detail": f"status={status} pub={bool(pub)}",
        }
    )
    stages.append(
        {
            "name": "blog_archive",
            "ok": False,
            "detail": "manual Owner check on Site/raw_feed (not auto-proven here)",
        }
    )
    stages.append(
        {
            "name": "source_telemetry",
            "ok": bool(health) or bool(report),
            "detail": f"health_rows={len(health)} collect_report={'yes' if report else 'no'}",
        }
    )
    stages.append(
        {
            "name": "semantic_event_id",
            "ok": bool(event_id) if semantic_dedup_enabled() else True,
            "detail": (
                f"event_id={event_id}"
                if event_id
                else ("flag off (SEMANTIC_DEDUP_ENABLED)" if not semantic_dedup_enabled() else "missing")
            ),
        }
    )

    critical = {"source_raw", "normalize_dedup_id", "owner_comment"}
    ok = all(s["ok"] for s in stages if s["name"] in critical)
    return {
        "item_id": item_id,
        "ok": ok,
        "status": status,
        "layers": {k: bool(v) for k, v in layers.items()},
        "stages": stages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Content E2E stage trace (no publish)")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--item-id", type=int)
    g.add_argument("--latest", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(_trace(args.item_id, args.latest))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"item_id={result.get('item_id')} overall={_ok(bool(result.get('ok')))}")
        for stage in result.get("stages") or []:
            print(
                f"  [{_ok(bool(stage.get('ok')))}] {stage.get('name')}: {stage.get('detail')}"
            )
        print(
            "Note: Blog/archive require Owner GO — see docs/integration/CONTROLLED_POST_E2E.md"
        )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
