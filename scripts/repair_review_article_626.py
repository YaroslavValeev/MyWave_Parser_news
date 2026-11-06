"""Save a reviewed, source-bound summary for item 626 without publishing or AI calls."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
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
from services.source_article import retrieve_article
from utils.source_context import article_urls, linked_article_context, source_input_hash

ITEM_ID = 626
URL = "https://wakeflot.ru/news/1786"
TITLE = "Консервация и зимнее хранение двигателей Malibu M5 M6"
TEXT_SHA256 = "ed67e8d158633b8169c51ea4a77c71ae85571131f116ef506f72d81e47402bdd"
MARKER = "reviewed-article-626-v1"
SUMMARY = (
    "Wakeflot опубликовал обзор подготовки Malibu Monsoon M5/M6 к зимнему хранению. "
    "Материал охватывает топливную систему, обслуживание масла и трансмиссии, слив забортной воды, "
    "балласт и аккумуляторы. При двухконтурном охлаждении консервация касается внешнего контура; "
    "антифриз из блока двигателя не сливают. Для M6Di с датой производства между 01.03.2021 и "
    "31.05.2024 статья указывает полностью синтетическое масло 0W-40 с допуском GM dexos R. "
    "Обработка цилиндров консервантом рассматривается как дополнительная мера. "
    "Распыление через дроссель при работающем двигателе запрещено."
)
FIELDS = (
    "summary",
    "questions",
    "decision",
    "moderation",
    "extra",
    "updated_at",
    "version",
)


class RepairRefused(RuntimeError):
    """Fixed refusal codes, without database content or credentials."""


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


@contextmanager
def connection(path, *, write=False):
    db = sqlite3.connect(
        Path(path).resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
        uri=True,
        timeout=10,
    )
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def snapshot(db):
    item = db.execute("SELECT * FROM items WHERE id=626").fetchone()
    nlp = db.execute("SELECT * FROM nlp_results WHERE item_id=626").fetchone()
    protected = db.execute("SELECT * FROM items WHERE id=649").fetchone()
    protected_nlp = db.execute("SELECT * FROM nlp_results WHERE item_id=649").fetchone()
    if item is None or nlp is None or protected is None:
        raise RepairRefused("target_record_missing")
    if item["status"] != "review" or protected["status"] != "discarded":
        raise RepairRefused("target_status_changed")
    counts = {
        item_id: db.execute(
            "SELECT COUNT(*) FROM publications WHERE item_id=?", (item_id,)
        ).fetchone()[0]
        for item_id in (626, 649)
    }
    if any(counts.values()):
        raise RepairRefused("target_has_publications")
    return {
        "item": dict(item),
        "nlp": dict(nlp),
        "protected_sha256": digest(
            {
                "item": dict(protected),
                "nlp": dict(protected_nlp) if protected_nlp else None,
            }
        ),
        "publication_count": 0,
    }


def extra_of(record):
    try:
        value = json.loads(record["nlp"].get("extra") or "{}")
    except (TypeError, ValueError) as exc:
        raise RepairRefused("invalid_nlp_extra") from exc
    if not isinstance(value, dict):
        raise RepairRefused("invalid_nlp_extra")
    return value


def audit(db, message):
    db.execute(
        "INSERT INTO logs(item_id,level,message,meta,created_at) VALUES(626,'info',?,?,?)",
        (
            message,
            json.dumps(
                {
                    "repair": MARKER,
                    "text_sha256": TEXT_SHA256,
                    "status_preserved": "review",
                }
            ),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def write_json(path, value):
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())


def update_nlp(db, values):
    fields = [name for name in FIELDS if name in values]
    cursor = db.execute(
        "UPDATE nlp_results SET "
        + ",".join(name + "=?" for name in fields)
        + " WHERE item_id=626",
        [values[name] for name in fields],
    )
    if cursor.rowcount != 1:
        raise RepairRefused("unexpected_row_count")


def result(state, **values):
    return {
        "review_repair": state,
        "item_id": ITEM_ID,
        "status_preserved": "review",
        "real_openai_requests": 0,
        "telegram_sends": 0,
        **values,
    }


async def repair(db_path, backup_parent, *, apply=False, retriever=retrieve_article):
    if (
        config.TEXT_MODEL != "gpt-4o-mini"
        or not config.SOURCE_ARTICLE_FETCH_ENABLED
        or "wakeflot.ru" not in config.SOURCE_ARTICLE_ALLOWED_HOSTS
    ):
        raise RepairRefused("configuration_changed")
    with connection(db_path) as db:
        db.execute("BEGIN")
        before = snapshot(db)
    item, old = before["item"], before["nlp"]
    extra = extra_of(before)
    if article_urls(item) != [URL]:
        raise RepairRefused("target_source_changed")
    existing = linked_article_context(item)
    if (
        old.get("summary") == SUMMARY
        and extra.get("review_source_repair") == MARKER
        and existing
        and existing["text_sha256"] == TEXT_SHA256
        and extra.get("source_text_sha256") == TEXT_SHA256
        and extra.get("source_input_sha256") == source_input_hash(item)
    ):
        return result("already_done", database_writes=0)
    if extra.get("owner_rewritten") or str(old.get("merged_text") or "").strip():
        raise RepairRefused("owner_final_text_present")
    evidence = await asyncio.wait_for(
        retriever(item, set(config.SOURCE_ARTICLE_ALLOWED_HOSTS)), timeout=30
    )
    evidence = linked_article_context({**item, "source_context": evidence})
    if (
        not evidence
        or evidence["text_sha256"] != TEXT_SHA256
        or evidence.get("title") != TITLE
    ):
        raise RepairRefused("article_evidence_changed")
    if not apply:
        with connection(db_path) as db:
            db.execute("BEGIN")
            if snapshot(db) != before:
                raise RepairRefused("target_changed_during_fetch")
        return result(
            "ready", database_writes=0, summary=SUMMARY, article_sha256=TEXT_SHA256
        )
    with connection(db_path, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        if snapshot(db) != before:
            raise RepairRefused("target_changed_during_fetch")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = Path(backup_parent) / ("review-article-626-" + stamp)
        backup.mkdir(parents=True, mode=0o700)
        os.chmod(backup, 0o700)
        write_json(backup / "before.json", before)
        for key in (
            "translated_text",
            "owner_editing_text",
            "owner_display_title",
            "event_id",
            "source_lang",
            "translation_skipped",
        ):
            extra.pop(key, None)
        extra.update(
            sanitized_text=evidence["text"],
            source_context_missing=False,
            source_input_sha256=source_input_hash(item),
            source_text_sha256=TEXT_SHA256,
            source_article_url=evidence["final_url"],
            review_source_repair=MARKER,
        )
        values = {
            "summary": SUMMARY,
            "questions": "[]",
            "decision": "review",
            "moderation": None,
            "extra": json.dumps(extra, ensure_ascii=False),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if "version" in old:
            values["version"] = old["version"] + 1
        context = json.dumps(evidence, ensure_ascii=False)
        db.execute("UPDATE items SET source_context=? WHERE id=626", (context,))
        update_nlp(db, values)
        after = snapshot(db)
        if (
            after["item"] != {**item, "source_context": context}
            or after["nlp"] != {**old, **values}
            or after["protected_sha256"] != before["protected_sha256"]
        ):
            raise RepairRefused("unexpected_record_change")
        audit(db, "review_article_nlp_repaired")
        write_json(backup / "after.json", after)
        write_json(
            backup / "state.json",
            {
                "marker": MARKER,
                "before_sha256": digest(before),
                "after_sha256": digest(after),
            },
        )
        db.commit()
    return result(
        "ok",
        backup=str(backup),
        summary=SUMMARY,
        database_writes=3,
        article_sha256=TEXT_SHA256,
    )


def rollback(db_path, backup_parent, directory):
    directory = Path(directory)
    if (
        directory.is_symlink()
        or directory.resolve().parent != Path(backup_parent).resolve()
        or not directory.name.startswith("review-article-626-")
    ):
        raise RepairRefused("unsafe_backup_path")
    data = {}
    for name in ("before", "after", "state"):
        path = directory / (name + ".json")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise RepairRefused("unsafe_backup_path")
        data[name] = json.loads(path.read_bytes())
    before, after, state = (data[name] for name in ("before", "after", "state"))
    if state != {
        "marker": MARKER,
        "before_sha256": digest(before),
        "after_sha256": digest(after),
    }:
        raise RepairRefused("backup_changed")
    if (
        before["item"]["id"] != ITEM_ID
        or after["item"]["id"] != ITEM_ID
        or before["nlp"]["item_id"] != ITEM_ID
    ):
        raise RepairRefused("backup_changed")
    with connection(db_path, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        if snapshot(db) != after:
            raise RepairRefused("rollback_target_changed")
        db.execute(
            "UPDATE items SET source_context=? WHERE id=626",
            (before["item"].get("source_context"),),
        )
        update_nlp(db, before["nlp"])
        if snapshot(db) != before:
            raise RepairRefused("unexpected_record_change")
        audit(db, "review_article_nlp_rolled_back")
        db.commit()
    return result("rolled_back", database_writes=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback-dir", type=Path)
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RepairRefused("run_as_root_in_server_project")
    os.umask(0o077)
    db_path, backups = ROOT / "data.db", ROOT.parent / "backups"
    if Path(config.DB_PATH).resolve() != db_path:
        raise RepairRefused("configuration_changed")
    output = (
        rollback(db_path, backups, args.rollback_dir)
        if args.rollback_dir
        else asyncio.run(repair(db_path, backups, apply=args.apply))
    )
    print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "review_repair": "failed",
                    "error_type": type(exc).__name__,
                    "check": str(exc)
                    if isinstance(exc, RepairRefused)
                    else "operation_failed",
                }
            ),
            flush=True,
        )
        raise SystemExit(1)
