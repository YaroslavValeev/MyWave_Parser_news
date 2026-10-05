"""Deploy the published source-context fix onto the verified CRLF server tree.

Application configuration, Git checkout and unrelated server code are preserved.
Rollback restores code; the safe redaction of item 649 remains in review.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path("/opt/bot3/parser-new-bot")
SERVICE = "parser-news-bot"
BASE = "0ff5a09b8fc702b6dcdffb03efad6cba09a6d70e"
FIX = "826cf722cca5dfa83a5c3ee3ba6a9b8fe8e391e1"
FILES = (
    "services/nlp_pipeline.py",
    "telegram_bot/views.py",
    "utils/item_context.py",
    "utils/owner_content.py",
)
# Raw blobs supplied by the server audit, including its CRLF line endings.
EXPECTED = dict(
    zip(
        FILES
        + (
            "services/publication.py",
            "storage/repository.py",
            "telegram_bot/router.py",
        ),
        (
            "f18b24240086a2249c453344551e0b9bacd2250e",
            "60de31455c3b8ef92674466553a263a79efaf8ff",
            "e37c0c28ce8eee9502b1cc995614968b731e3cc2",
            "57078f25edd9d1106d91bf46c66c32558f04c583",
            "ac799f680fce2450b8dc9684b7180e928a3c6341",
            "5b34efc75494442caca8ff83c318540ecfd061f9",
            "5ef3e3ff2a3fa468648080b03198782ecadf09fb",
        ),
    )
)


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def verify_files(root, expected):
    for name, digest in expected.items():
        if blob((root / name).read_bytes()) != digest:
            raise RuntimeError("server_file_changed:" + name)


def prepare_code(root, staging, patch):
    """Apply context-checked hunks to LF temporary files, then restore each style."""
    originals = {}
    for name in FILES:
        raw = (root / name).read_bytes()
        originals[name] = raw
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw.replace(b"\r\n", b"\n"))
    old = "<b>Саммари (NLP)</b>".encode()
    new = "<b>Кратко (факты)</b>".encode()
    if patch.count(old) != 1:
        raise RuntimeError("unexpected_patch_context")
    patch_path = staging / "source-context.patch"
    patch_path.write_bytes(patch.replace(old, new))
    for options in (("--check",), ()):
        subprocess.run(
            ["git", "apply", *options, str(patch_path)],
            cwd=staging,
            check=True,
            capture_output=True,
            timeout=15,
        )
    prepared = {}
    for name, original in originals.items():
        raw = (staging / name).read_bytes().replace(b"\r\n", b"\n")
        ast.parse(raw.decode("utf-8"), filename=name)
        prepared[name] = raw.replace(b"\n", b"\r\n") if b"\r\n" in original else raw
    return prepared


def install_file(path, raw, metadata=None):
    metadata = metadata or path.stat()
    fd, temporary = tempfile.mkstemp(prefix=".context-hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, metadata.st_mode & 0o7777)
        if hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def target_rows(db):
    item = db.execute("SELECT * FROM items WHERE id=649").fetchone()
    nlp = db.execute("SELECT * FROM nlp_results WHERE item_id=649").fetchone()
    if item is None or nlp is None:
        raise RuntimeError("target_record_missing")
    item, nlp = dict(item), dict(nlp)
    if item.get("status") != "review":
        raise RuntimeError("target_status_changed")
    if (
        str(item.get("content") or "").strip().rstrip("/")
        != "https://wakeflot.ru/news/1785"
    ):
        raise RuntimeError("target_source_changed")
    if str(item.get("transcript") or "").strip():
        raise RuntimeError("target_transcript_changed")
    extra = json.loads(nlp.get("extra") or "{}")
    if not isinstance(extra, dict) or extra.get("owner_rewritten") is True:
        raise RuntimeError("target_owner_rewrite_changed")
    return item, nlp, extra


def open_db(path, mode="ro"):
    db = sqlite3.connect(
        path.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=10
    )
    db.row_factory = sqlite3.Row
    return db


def repair_record(path):
    """Redact the stale derived result; preserve source, owner notes and media."""
    from utils.item_context import missing_text_context_summary

    db = open_db(path, "rw")
    try:
        db.execute("BEGIN IMMEDIATE")
        item, old, extra = target_rows(db)
        for key in ("translated_text", "owner_editing_text", "owner_display_title"):
            extra.pop(key, None)
        extra.update(sanitized_text="", source_context_missing=True)
        version = ", version=version+1" if "version" in old else ""
        db.execute(
            "UPDATE nlp_results SET summary=?, questions=?, decision=?, extra=?, "
            "merged_text=NULL, updated_at=?" + version + " WHERE item_id=649",
            (
                missing_text_context_summary(item),
                "[]",
                "review",
                json.dumps(extra, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        new = dict(db.execute("SELECT * FROM nlp_results WHERE item_id=649").fetchone())
        if new.get("author_notes") != old.get("author_notes"):
            raise RuntimeError("owner_notes_changed")
        if new.get("voice_file") != old.get("voice_file"):
            raise RuntimeError("owner_voice_changed")
        db.commit()
    finally:
        db.close()


def service(*args):
    return subprocess.run(
        ["systemctl", *args, SERVICE],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def start_and_check():
    service("start")
    time.sleep(2)
    service("is-active", "--quiet")
    pid = service("show", "--property=MainPID", "--value")
    if not pid.isdigit() or int(pid) <= 0:
        raise RuntimeError("service_pid_missing")
    time.sleep(6)
    service("is-active", "--quiet")
    if service("show", "--property=MainPID", "--value") != pid:
        raise RuntimeError("service_restarted_during_check")
    return pid


def restore_code(root, backup):
    for name in FILES:
        install_file(root / name, (backup / "code" / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback-code", type=Path)
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("run_as_root_on_server")
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if (
        Path(service("show", "--property=WorkingDirectory", "--value")).resolve()
        != ROOT
    ):
        raise RuntimeError("service_directory_changed")
    if args.rollback_code:
        backup = args.rollback_code.resolve()
        manifest = json.loads((backup / "manifest.json").read_text())
        if manifest["root"] != str(ROOT):
            raise RuntimeError("backup_root_mismatch")
        verify_files(ROOT, manifest["after"])
        service("stop")
        restore_code(ROOT, backup)
        emit(code_rollback="ok", item_649_redaction="retained", pid=start_and_check())
        return

    from config.settings import config

    db_path = Path(config.DB_PATH).resolve()
    if db_path != ROOT / "data.db" or config.TEXT_MODEL != "gpt-4o-mini":
        raise RuntimeError("audited_configuration_changed")
    verify_files(ROOT, EXPECTED)
    service("is-active", "--quiet")
    db = open_db(db_path)
    try:
        target_rows(db)
    finally:
        db.close()
    patch = subprocess.check_output(
        ["git", "diff", "--binary", BASE, FIX, "--", *FILES],
        timeout=15,
    )
    with tempfile.TemporaryDirectory(prefix="mywave-context-stage-") as directory:
        prepared = prepare_code(ROOT, Path(directory), patch)
    emit(patch_check="ok", files=list(FILES), item_id=649, text_model=config.TEXT_MODEL)
    if not args.apply:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = ROOT.parent / "backups" / ("source-context-649-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    manifest = {
        "root": str(ROOT),
        "before": EXPECTED,
        "after": {name: blob(raw) for name, raw in prepared.items()},
    }
    for name in FILES:
        destination = backup / "code" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    shutil.copy2(__file__, backup / "deploy.py")
    emit(backup=str(backup))
    stopped = False
    repaired = False
    try:
        verify_files(ROOT, EXPECTED)
        stopped = True
        service("stop")
        db = open_db(db_path)
        snapshot = sqlite3.connect(backup / "data.sqlite")
        try:
            target_rows(db)
            db.backup(snapshot)
        finally:
            snapshot.close()
            db.close()
        for name, raw in prepared.items():
            install_file(ROOT / name, raw)
        verify_files(ROOT, manifest["after"])
        from telegram_bot.views import build_review_card_html
        from utils.item_context import get_item_text_context

        if get_item_text_context({"content": "https://wakeflot.ru/news/1785"}):
            raise RuntimeError("source_guard_failed")
        card = build_review_card_html(
            {
                "title": "Wakeflot",
                "content": "https://wakeflot.ru/news/1785",
                "link": "https://t.me/Wakeflot/3048",
            },
            {"summary": "Чемпионат 2023", "merged_text": "Чемпионат 2023"},
        )
        if "Чемпионат 2023" in card:
            raise RuntimeError("card_guard_failed")
        repair_record(db_path)
        repaired = True
        pid = start_and_check()
        emit(
            deploy="ok",
            item_id=649,
            status="review",
            source_context_missing=True,
            pid=pid,
            text_model=config.TEXT_MODEL,
            rollback_command=f"venv/bin/python -B {backup}/deploy.py --rollback-code {backup}",
        )
    except BaseException as exc:
        emit(deploy="failed", error_type=type(exc).__name__)
        if stopped:
            service("stop")
            restore_code(ROOT, backup)
            emit(
                code_rollback="ok",
                item_649_redaction_retained=repaired,
                pid=start_and_check(),
            )
        raise


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        emit(error_type=type(exc).__name__)
        sys.exit(1)
