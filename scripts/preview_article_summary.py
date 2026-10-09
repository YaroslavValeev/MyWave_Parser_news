"""Preview the configured 4o mini summary without changing item data or sending messages."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import sys
import warnings

ROOT = Path("/opt/bot3/parser-new-bot")
sys.path.insert(0, str(ROOT if ROOT.is_dir() else Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
warnings.filterwarnings(
    "ignore", message="The input passed in on this line looks more like a URL.*"
)

from config.settings import config
from nlp.openai_client import OpenAIClient
from services.source_article import retrieve_article
from utils.source_context import article_urls, linked_article_context, source_input_hash


class PreviewRefused(ValueError):
    pass


def read_item(db_path, item_id):
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise PreviewRefused("item_missing")
    return dict(row)


async def preview(
    db_path, item_id, expected_url, *, client=None, retriever=retrieve_article
):
    if item_id == 649:
        raise PreviewRefused("protected_item")
    item = read_item(db_path, item_id)
    if item.get("status") not in {"new", "review", "deferred"}:
        raise PreviewRefused("item_not_eligible")
    if config.TEXT_MODEL != "gpt-4o-mini":
        raise PreviewRefused("model_changed")
    if article_urls(item) != [expected_url]:
        raise PreviewRefused("unexpected_source")
    print(json.dumps({"preview_phase": "article", "item_id": item_id}), flush=True)
    evidence = await retriever(item, set(config.SOURCE_ARTICLE_ALLOWED_HOSTS))
    if linked_article_context({**item, "source_context": evidence}) is None:
        raise PreviewRefused("invalid_source_evidence")
    ai = client or OpenAIClient()
    sdk = await ai._ensure_client()
    try:
        print(json.dumps({"preview_phase": "summary", "item_id": item_id}), flush=True)
        summary = await ai.summarize(evidence["text"], lang="ru", max_words=120)
    finally:
        await asyncio.wait_for(sdk.close(), timeout=5)
    current = read_item(db_path, item_id)
    if source_input_hash(current) != source_input_hash(item) or current.get(
        "status"
    ) != item.get("status"):
        raise PreviewRefused("source_or_status_changed")
    return {
        "summary_preview": "ok",
        "item_id": item_id,
        "status_preserved": current["status"],
        "text_model": config.TEXT_MODEL,
        "source_url": evidence["final_url"],
        "article_title": evidence["title"],
        "article_chars": len(evidence["text"]),
        "text_sha256": evidence["text_sha256"],
        "summary": summary,
        "database_writes": 0,
        "telegram_sends": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-id", type=int, required=True)
    parser.add_argument("--expected-url", required=True)
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT:
        raise PreviewRefused("run_in_server_project")
    result = asyncio.run(
        asyncio.wait_for(
            preview(config.DB_PATH, args.item_id, args.expected_url), timeout=120
        )
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        result = {
            "summary_preview": "failed",
            "error_type": type(exc).__name__,
            "http_status": getattr(exc, "status_code", None),
        }
        if isinstance(exc, PreviewRefused):
            result["check"] = str(exc)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        raise SystemExit(1)
