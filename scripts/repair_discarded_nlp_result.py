"""Redact only item 649's unsupported NLP result, preserving its rejection."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit


ROOT = Path("/opt/bot3/parser-new-bot")
ITEM_ID = 649


class RepairRefusal(RuntimeError):
    """A safe, predefined refusal code suitable for operator output."""


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def repair(db_path, backup_parent, *, apply=False):
    from nlp.sanitize import sanitize_text
    from utils.item_context import get_item_text_context, missing_text_context_summary

    db = sqlite3.connect(
        db_path.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro"),
        uri=True,
        timeout=10,
    )
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        row = db.execute("SELECT * FROM items WHERE id=?", (ITEM_ID,)).fetchone()
        nlp_row = db.execute(
            "SELECT * FROM nlp_results WHERE item_id=?", (ITEM_ID,)
        ).fetchone()
        if row is None or nlp_row is None:
            raise RepairRefusal("target_record_missing")
        item, old = dict(row), dict(nlp_row)
        if item.get("status") != "discarded":
            raise RepairRefusal("target_status_changed")
        count = db.execute(
            "SELECT COUNT(*) FROM publications WHERE item_id=?", (ITEM_ID,)
        ).fetchone()[0]
        if count:
            raise RepairRefusal("target_has_publications")
        if get_item_text_context(item):
            raise RepairRefusal("target_has_source_text")
        urls = re.findall(
            r'https?://[^\s<>"\'\u200b]+', sanitize_text(item.get("content"))
        )
        if not any(
            urlsplit(url).hostname in {"wakeflot.ru", "www.wakeflot.ru"}
            and urlsplit(url).path.rstrip("/") == "/news/1785"
            for url in urls
        ):
            raise RepairRefusal("target_source_changed")
        extra = json.loads(old.get("extra") or "{}")
        if not isinstance(extra, dict) or extra.get("owner_rewritten") is True:
            raise RepairRefusal("target_owner_rewrite_changed")
        bad_summary = "чемпионат" in str(old.get("summary") or "").casefold()
        if extra.get("source_context_missing") is True and not bad_summary:
            return {
                "repair": "already_done",
                "item_id": ITEM_ID,
                "status_preserved": "discarded",
            }
        if not bad_summary:
            raise RepairRefusal("target_summary_changed")
        if not apply:
            return {
                "repair": "ready",
                "item_id": ITEM_ID,
                "status_preserved": "discarded",
            }

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = backup_parent / ("discarded-nlp-649-" + stamp)
        backup.mkdir(parents=True, mode=0o700)
        os.chmod(backup, 0o700)
        with (backup / "before.json").open("x", encoding="utf-8") as handle:
            json.dump(
                {"item": item, "nlp": old, "publication_count": count},
                handle,
                ensure_ascii=False,
                indent=2,
            )
        os.chmod(backup / "before.json", 0o600)

        for key in (
            "translated_text",
            "owner_editing_text",
            "owner_display_title",
            "event_id",
        ):
            extra.pop(key, None)
        extra.update(sanitized_text="", source_context_missing=True)
        version = ", version=version+1" if "version" in old else ""
        result = db.execute(
            "UPDATE nlp_results SET summary=?, questions='[]', decision='review', "
            "moderation=NULL, extra=?, merged_text=NULL, updated_at=?"
            + version
            + " WHERE item_id=?",
            (
                missing_text_context_summary(item),
                json.dumps(extra, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
                ITEM_ID,
            ),
        )
        if result.rowcount != 1:
            raise RepairRefusal("unexpected_row_count")
        new = dict(
            db.execute(
                "SELECT * FROM nlp_results WHERE item_id=?", (ITEM_ID,)
            ).fetchone()
        )
        for key in ("author_notes", "voice_file", "rewrite_guidance"):
            if new.get(key) != old.get(key):
                raise RepairRefusal("owner_fields_changed")
        if (
            dict(db.execute("SELECT * FROM items WHERE id=?", (ITEM_ID,)).fetchone())
            != item
        ):
            raise RepairRefusal("item_fields_changed")
        db.commit()
        return {
            "repair": "ok",
            "item_id": ITEM_ID,
            "status_preserved": "discarded",
            "publication_count": 0,
            "source_context_missing": True,
            "backup": str(backup / "before.json"),
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RepairRefusal("run_as_root_on_server")
    os.umask(0o077)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    result = repair(ROOT / "data.db", ROOT.parent / "backups", apply=args.apply)
    emit(**result)
    if result["repair"] == "ok":
        from storage.repository import AsyncNewsRepository

        try:
            asyncio.run(
                AsyncNewsRepository(ROOT / "data.db").log_event(
                    ITEM_ID,
                    "warning",
                    "owner_nlp_untrusted_result_redacted",
                    {"reason": "missing_source_text", "status_preserved": "discarded"},
                )
            )
            emit(audit_log="ok")
        except Exception as exc:
            emit(audit_log="failed", error_type=type(exc).__name__)


if __name__ == "__main__":
    try:
        main()
    except RepairRefusal as exc:
        emit(error_type="RuntimeError", check=str(exc))
        sys.exit(1)
    except Exception as exc:
        emit(error_type=type(exc).__name__)
        sys.exit(1)
