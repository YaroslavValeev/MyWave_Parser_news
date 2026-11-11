#!/usr/bin/env python3
"""Read-only dry-run classification of publishable raw_feed media rows.

Never writes to Google Sheets. Produces Markdown + CSV report for Owner/GM GO.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.media_utils import (  # noqa: E402
    build_media_contract_diagnostic,
    extract_raw_feed_cover_image_url,
)
from utils.video_providers import detect_provider, resolve_video_media  # noqa: E402

PUBLISHABLE_STATUSES = {"READY_TO_PUBLISH", "PUBLISHED"}
VIDEO_IN_TEXT = re.compile(
    r"https?://[^\s\]\)>'\"]*(youtube\.com|youtu\.be|rutube\.ru|vimeo\.com|kinescope\.io|vk\.(com|ru)/video|\.mp4|\.webm)",
    re.IGNORECASE,
)


def _cell(row: list[str], header: list[str], name: str) -> str:
    try:
        index = header.index(name)
    except ValueError:
        return ""
    return row[index] if len(row) > index else ""


def _row_to_dict(row: list[str], header: list[str]) -> dict[str, str]:
    return {name: _cell(row, header, name) for name in header if name}


def _looks_local(value: str) -> bool:
    text = (value or "").strip().replace("\\", "/")
    if not text:
        return False
    if "127.0.0.1" in text or "localhost" in text:
        return True
    return text.startswith(("./", "../", "downloads/", "media/", "/static/downloads/")) or bool(
        re.match(r"^[A-Za-z]:/", text)
    )


def classify_row(row: dict[str, str]) -> dict[str, Any]:
    status = str(row.get("status") or "").strip().upper()
    cover = str(row.get("cover_image_url") or "").strip() or extract_raw_feed_cover_image_url(row)
    video = resolve_video_media(row, poster_url=cover)
    diagnostic = build_media_contract_diagnostic(row)
    body = "\n".join(
        str(row.get(key) or "")
        for key in ("content_md", "final_posts", "text", "final_version", "raw_content")
    )
    flags: list[str] = []
    if video.video_url or video.embed_url:
        flags.append("has_structured_video")
    if VIDEO_IN_TEXT.search(body) and not (video.video_url or video.embed_url):
        flags.append("video_only_in_content")
    if diagnostic.media_status in {"failed", "unsupported"} or _looks_local(cover):
        flags.append("invalid_media")
    if _looks_local(cover) or _looks_local(str(row.get("raw_media") or "")):
        flags.append("local_path")
    if (video.video_url or video.embed_url) and not (video.poster_url or cover):
        flags.append("missing_poster")
    if not str(row.get("source_name") or "").strip() or not str(row.get("source_url") or "").strip():
        flags.append("missing_source_attribution")
    if not flags:
        flags.append("ok")
    return {
        "id": row.get("id") or "",
        "slug": row.get("slug") or "",
        "status": status,
        "title": (row.get("raw_title") or row.get("title") or "")[:90],
        "cover_image_url": cover[:160],
        "video_url": video.video_url,
        "embed_url": video.embed_url,
        "media_status": diagnostic.media_status,
        "media_error": diagnostic.media_error,
        "provider": video.provider or detect_provider(video.video_url or video.embed_url or ""),
        "flags": ",".join(flags),
        "proposed_writes": 0,
    }


def _load_from_sheets(limit: int) -> list[dict[str, str]]:
    from utils.sheet_gateway import get_worksheet, init_sheet_gateway

    init_sheet_gateway()
    ws = get_worksheet()
    values = ws.get_all_values()
    if not values:
        return []
    header = [str(h).strip() for h in values[0]]
    rows: list[dict[str, str]] = []
    for raw in values[1:]:
        item = _row_to_dict(raw, header)
        status = str(item.get("status") or "").strip().upper()
        if status not in PUBLISHABLE_STATUSES:
            continue
        rows.append(item)
        if limit and len(rows) >= limit:
            break
    return rows


def _load_from_json(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        payload = payload["rows"]
    if not isinstance(payload, list):
        raise ValueError("JSON must be a list of row objects")
    return [{str(k): str(v or "") for k, v in row.items()} for row in payload if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run blog media backfill report (read-only)")
    parser.add_argument("--source", choices=("sheets", "json"), default="json")
    parser.add_argument("--json-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "docs/integration/artifacts",
    )
    args = parser.parse_args()

    if args.source == "sheets":
        rows = _load_from_sheets(args.limit)
    else:
        json_path = args.json_file or (
            PROJECT_ROOT / "docs/integration/artifacts/blog-media-backfill-candidates-20260616.json"
        )
        if not json_path.is_file():
            # Synthetic sample so the script is runnable offline.
            rows = [
                {
                    "id": "sample-1",
                    "slug": "sample-youtube",
                    "status": "READY_TO_PUBLISH",
                    "raw_title": "Sample with youtube in body",
                    "content_md": "See https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "source_name": "",
                    "source_url": "",
                    "cover_image_url": "downloads/review_media/x.jpg",
                },
                {
                    "id": "sample-2",
                    "slug": "sample-ok",
                    "status": "PUBLISHED",
                    "raw_title": "OK external cover",
                    "content_md": "Paragraph one.\n\nParagraph two.",
                    "source_name": "Wake Division",
                    "source_url": "https://example.com/post",
                    "cover_image_url": "https://cdn.example.com/cover.jpg",
                    "video_url": "https://www.youtube.com/watch?v=abcdefghijk",
                },
            ]
        else:
            rows = _load_from_json(json_path)
            if args.limit:
                rows = rows[: args.limit]

    classified = [classify_row(row) for row in rows]
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = args.out / f"BACKFILL_DRY_RUN_{stamp}.csv"
    md_path = args.out / f"BACKFILL_DRY_RUN_{stamp}.md"

    fieldnames = list(classified[0].keys()) if classified else [
        "id",
        "slug",
        "status",
        "flags",
        "proposed_writes",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(classified)

    counts: dict[str, int] = {}
    for row in classified:
        for flag in str(row.get("flags") or "").split(","):
            if flag:
                counts[flag] = counts.get(flag, 0) + 1

    lines = [
        f"# Blog media backfill dry-run ({stamp})",
        "",
        f"- rows: {len(classified)}",
        f"- proposed_writes: 0",
        f"- csv: `{csv_path.as_posix()}`",
        "",
        "## Flag counts",
        "",
    ]
    for key, value in sorted(counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Rows", ""])
    for row in classified:
        lines.append(
            f"- id={row.get('id')} slug={row.get('slug')} flags={row.get('flags')} "
            f"media_status={row.get('media_status')}"
        )
    lines.append("")
    lines.append("**Guardrail:** mass Sheet writeback запрещён до Owner/GM GO.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {md_path}")
    print(f"wrote {csv_path}")
    print(f"rows={len(classified)} proposed_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
