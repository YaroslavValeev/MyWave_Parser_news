"""Audited overlay deployment: preserve server media code, model, Owner data and Git state."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.request import urlopen

ROOT = Path("/opt/bot3/parser-new-bot")
BACKUPS = Path("/opt/bot3/backups")
SERVICE = "parser-news-bot"
MANIFEST_SHA256 = "f11778edc239aecb4d562b954a41e82491b9b180ab4afb702ebfb30a13a60139"
SAFE_ERRORS = {
    "unsafe_release_path",
    "release_artifact_hash_mismatch",
    "sqlite_check_failed",
    "target_owner_state_changed",
    "offline_smoke_failed",
    "service_pid_missing",
    "service_unstable",
    "configuration_changed",
    "invalid_package_ref",
    "run_as_root_in_project",
    "service_directory_changed",
    "source_probe_required",
    "source_probe_failed",
    "unsafe_backup_path",
    "backup_file_changed",
    "source_activation_changed",
}


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def lf(raw):
    return raw.replace(b"\r\n", b"\n")


def run(args, *, cwd=ROOT, timeout=60, env=None):
    return (
        subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, check=True, timeout=timeout
        )
        .stdout.decode("utf-8", errors="replace")
        .strip()
    )


def service(*args):
    return run(["systemctl", *args, SERVICE], timeout=30)


def path_for(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise RuntimeError("unsafe_release_path")
    return path


def fetch(url, expected):
    with urlopen(url, timeout=30) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or digest(raw) != expected:
        raise RuntimeError("release_artifact_hash_mismatch")
    return raw


def verify(root, expected):
    for name, expected_hash in expected.items():
        path = path_for(root, name)
        if not path.is_file() or digest(lf(path.read_bytes())) != expected_hash:
            raise RuntimeError("file_changed:" + name)


def prepare(root, stage, manifest, patch):
    verify(root, manifest["before"])
    verify(root, manifest["guard"])
    for name in set(manifest["after"]) - set(manifest["before"]):
        if path_for(root, name).exists():
            raise RuntimeError("new_file_already_exists:" + name)
    # Copy code only. Production .env, SQLite, downloads and Git never enter staging.
    excluded = {
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        "downloads",
        "static",
        "backups",
        "logs",
    }
    for directory, directories, filenames in os.walk(root):
        directories[:] = [
            name
            for name in directories
            if name not in excluded and not (Path(directory) / name).is_symlink()
        ]
        for name in filenames:
            source = Path(directory) / name
            if source.suffix in {".py", ".sql"} and not source.is_symlink():
                target = path_for(stage, str(source.relative_to(root)))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(lf(source.read_bytes()))
    patch_path = stage / "overlay.patch"
    patch_path.write_bytes(patch)
    for options in (("--check",), ()):
        run(["git", "apply", *options, str(patch_path)], cwd=stage, timeout=15)
    verify(stage, manifest["after"])
    for name in manifest["after"]:
        if name.endswith(".py"):
            ast.parse((stage / name).read_text(encoding="utf-8"), filename=name)
    return {name: lf((stage / name).read_bytes()) for name in manifest["after"]}


def install(path, raw, metadata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".editorial-release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, metadata.st_mode & 0o7777 if metadata else 0o644)
        if metadata and hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def target_snapshot(db_path):
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("sqlite_check_failed")
        item = db.execute("SELECT * FROM items WHERE id=649").fetchone()
        nlp = db.execute("SELECT * FROM nlp_results WHERE item_id=649").fetchone()
        count = db.execute(
            "SELECT COUNT(*) FROM publications WHERE item_id=649"
        ).fetchone()[0]
        if not item or item["status"] != "discarded" or count:
            raise RuntimeError("target_owner_state_changed")
        original_item = dict(item)
        original_item.pop("source_context", None)
        return digest(
            json.dumps(
                {
                    "item": original_item,
                    "nlp": dict(nlp) if nlp else None,
                    "publications": count,
                },
                sort_keys=True,
                default=str,
            ).encode()
        )


def smoke(root):
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"
    )
    output = run(
        [
            str(ROOT / "venv/bin/python"),
            "-B",
            str(root / "scripts/editorial_offline_smoke.py"),
        ],
        cwd=root,
        timeout=60,
        env=environment,
    )
    result = json.loads(output.splitlines()[-1])
    if result != {
        "offline_smoke": "ok",
        "cases": 2,
        "real_telegram_sends": 0,
        "real_openai_requests": 0,
    }:
        raise RuntimeError("offline_smoke_failed")


def health():
    service("is-active", "--quiet")
    pid = service("show", "--property=MainPID", "--value")
    if not pid.isdigit() or int(pid) < 1:
        raise RuntimeError("service_pid_missing")
    time.sleep(5)
    service("is-active", "--quiet")
    if service("show", "--property=MainPID", "--value") != pid:
        raise RuntimeError("service_unstable")
    return pid


def set_hosts(env_path, hosts):
    from dotenv import set_key

    metadata = env_path.stat()
    set_key(
        str(env_path),
        "SOURCE_ARTICLE_ALLOWED_HOSTS",
        ",".join(hosts),
        quote_mode="never",
    )
    set_key(str(env_path), "SOURCE_ARTICLE_FETCH_ENABLED", "true", quote_mode="never")
    os.chmod(env_path, metadata.st_mode & 0o7777)
    if hasattr(os, "chown"):
        os.chown(env_path, metadata.st_uid, metadata.st_gid)


def restore_hosts(env_path, old_raw):
    """Rollback only our two public flags, retaining independently changed secrets."""
    from dotenv import dotenv_values, set_key, unset_key

    metadata = env_path.stat()
    old = dotenv_values(stream=io.StringIO(old_raw.decode("utf-8")))
    for key in ("SOURCE_ARTICLE_ALLOWED_HOSTS", "SOURCE_ARTICLE_FETCH_ENABLED"):
        if key in old and old[key] is not None:
            set_key(str(env_path), key, old[key], quote_mode="auto")
        else:
            unset_key(str(env_path), key)
    os.chmod(env_path, metadata.st_mode & 0o7777)
    if hasattr(os, "chown"):
        os.chown(env_path, metadata.st_uid, metadata.st_gid)


def deploy(manifest, prepared, db_path, snapshot, *, hosts=()):
    backup = BACKUPS / (
        "editorial-release-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    backup.mkdir(parents=True, mode=0o700)
    originals, metadata = {}, {}
    for name in manifest["after"]:
        path = path_for(ROOT, name)
        originals[name] = path.read_bytes() if path.exists() else None
        metadata[name] = path.stat() if path.exists() else None
        if originals[name] is not None:
            destination = backup / "code" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(originals[name])
    env_path = path_for(ROOT, ".env")
    env_raw = env_path.read_bytes()
    env_metadata = env_path.stat()
    (backup / "before.env").write_bytes(env_raw)
    os.chmod(backup / "before.env", 0o600)
    stopped, mutated, env_modified = False, False, False
    try:
        verify(ROOT, manifest["before"])
        verify(ROOT, manifest["guard"])
        if env_path.read_bytes() != env_raw:
            raise RuntimeError("configuration_changed")
        stopped = True
        service("stop")
        verify(ROOT, manifest["before"])
        verify(ROOT, manifest["guard"])
        if env_path.read_bytes() != env_raw:
            raise RuntimeError("configuration_changed")
        if target_snapshot(db_path) != snapshot:
            raise RuntimeError("target_owner_state_changed")
        with (
            sqlite3.connect(db_path) as source,
            sqlite3.connect(backup / "data.sqlite") as target,
        ):
            source.backup(target)
        os.chmod(backup / "data.sqlite", 0o600)
        mutated = True
        for name, raw in prepared.items():
            old = originals[name]
            if old is not None and b"\r\n" in old:
                raw = lf(raw).replace(b"\n", b"\r\n")
            install(ROOT / name, raw, metadata[name])
        run(
            [
                str(ROOT / "venv/bin/python"),
                "-B",
                "-c",
                "import asyncio;from config.settings import config;from storage.repository import initialize_database;asyncio.run(initialize_database(config.DB_PATH))",
            ]
        )
        smoke(ROOT)
        if hosts:
            if env_path.read_bytes() != env_raw:
                raise RuntimeError("configuration_changed")
            env_modified = True
            set_hosts(env_path, hosts)
        verify(ROOT, manifest["after"])
        verify(ROOT, manifest["guard"])
        # Migration adds a nullable column; compare original evidence while excluding it.
        if target_snapshot(db_path) != snapshot:
            raise RuntimeError("target_owner_state_changed")
        state = {
            "root": str(ROOT),
            "release": manifest,
            "env_after_sha256": digest(env_path.read_bytes()),
            "env_metadata": {
                "mode": env_metadata.st_mode,
                "uid": env_metadata.st_uid,
                "gid": env_metadata.st_gid,
            },
            "metadata": {
                name: {"mode": stat.st_mode, "uid": stat.st_uid, "gid": stat.st_gid}
                if stat
                else None
                for name, stat in metadata.items()
            },
        }
        (backup / "manifest.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
        service("start")
        pid = health()
        if target_snapshot(db_path) != snapshot:
            raise RuntimeError("target_owner_state_changed")
        emit(
            release="ok",
            backup=str(backup),
            main_pid=pid,
            text_model="gpt-4o-mini",
            source_hosts=list(hosts),
            item_649_status="discarded",
            real_test_sends=0,
        )
    except BaseException:
        if stopped:
            service("stop")
        if mutated:
            for name, raw in originals.items():
                path = ROOT / name
                if raw is None:
                    if path.exists():
                        path.unlink()
                else:
                    install(path, raw, metadata[name])
            if env_modified:
                restore_hosts(env_path, env_raw)
        if stopped:
            service("start")
        emit(release="rolled_back", backup=str(backup), database_restored=False)
        raise


def rollback(directory):
    backup = Path(directory).resolve()
    if (
        not backup.is_relative_to(BACKUPS.resolve())
        or backup == BACKUPS.resolve()
        or not backup.name.startswith("editorial-release-")
    ):
        raise RuntimeError("unsafe_backup_path")
    state = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if state["root"] != str(ROOT):
        raise RuntimeError("unsafe_backup_path")
    manifest = state["release"]
    verify(ROOT, manifest["after"])
    verify(ROOT, manifest["guard"])
    env_path = path_for(ROOT, ".env")
    if digest(env_path.read_bytes()) != state["env_after_sha256"]:
        raise RuntimeError("configuration_changed")
    for name, expected in manifest["before"].items():
        if digest(lf(path_for(backup / "code", name).read_bytes())) != expected:
            raise RuntimeError("backup_file_changed")
    service("stop")
    try:
        for name in manifest["after"]:
            original = state["metadata"][name]
            if original is None:
                path_for(ROOT, name).unlink()
            else:
                metadata = SimpleNamespace(
                    st_mode=original["mode"],
                    st_uid=original["uid"],
                    st_gid=original["gid"],
                )
                install(ROOT / name, (backup / "code" / name).read_bytes(), metadata)
        original = state["env_metadata"]
        metadata = SimpleNamespace(
            st_mode=original["mode"], st_uid=original["uid"], st_gid=original["gid"]
        )
        install(env_path, (backup / "before.env").read_bytes(), metadata)
    finally:
        service("start")
    emit(rollback="ok", main_pid=health(), database_restored=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback-dir")
    parser.add_argument("--package-ref")
    parser.add_argument("--enable-host", action="append", default=[])
    parser.add_argument("--probe-url")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0 or Path.cwd().resolve() != ROOT:
        raise RuntimeError("run_as_root_in_project")
    os.umask(0o077)

    def interrupted(signum, frame):
        raise InterruptedError("deployment_interrupted")

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    if service("show", "--property=WorkingDirectory", "--value") != str(ROOT):
        raise RuntimeError("service_directory_changed")
    service("is-active", "--quiet")
    if args.rollback_dir:
        rollback(args.rollback_dir)
        return
    if not args.package_ref or not re.fullmatch(r"[0-9a-f]{40}", args.package_ref):
        raise RuntimeError("invalid_package_ref")
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(ROOT))
    from config.settings import config

    db_path = Path(config.DB_PATH).resolve()
    if db_path != ROOT / "data.db" or config.TEXT_MODEL != "gpt-4o-mini":
        raise RuntimeError("configuration_changed")
    hosts = tuple(value.lower() for value in args.enable_host)
    if hosts and (
        len(hosts) != 1
        or not args.probe_url
        or any(not re.fullmatch(r"[a-z0-9.-]+", value) for value in hosts)
    ):
        raise RuntimeError("source_probe_required")
    base = (
        "https://raw.githubusercontent.com/YaroslavValeev/MyWave_Parser_news/"
        + args.package_ref
        + "/scripts/releases/editorial-4o-mini/"
    )
    manifest = json.loads(fetch(base + "manifest.json", MANIFEST_SHA256))
    patch = fetch(base + "overlay.patch", manifest["patch_sha256"])
    emit(
        audit="runtime",
        file_hashes={
            name: digest(lf((ROOT / name).read_bytes()))
            if (ROOT / name).is_file()
            else None
            for name in manifest["after"]
        },
        packages={
            name: importlib.metadata.version(name)
            for name in ("openai", "httpx", "aiogram", "aiosqlite")
        },
    )
    snapshot = target_snapshot(db_path)
    already_installed = all(
        (ROOT / name).is_file() and digest(lf((ROOT / name).read_bytes())) == expected
        for name, expected in manifest["after"].items()
    )
    if already_installed:
        verify(ROOT, manifest["guard"])
        smoke(ROOT)
        if hosts and (
            not config.SOURCE_ARTICLE_FETCH_ENABLED
            or set(hosts) != set(config.SOURCE_ARTICLE_ALLOWED_HOSTS)
        ):
            raise RuntimeError("source_activation_changed")
        emit(
            release="already_installed",
            main_pid=health(),
            text_model=config.TEXT_MODEL,
            item_649_status="discarded",
        )
        return
    with tempfile.TemporaryDirectory(prefix="mywave-editorial-stage-") as directory:
        stage = Path(directory)
        prepared = prepare(ROOT, stage, manifest, patch)
        smoke(stage)
        if hosts:
            result = json.loads(
                run(
                    [
                        str(ROOT / "venv/bin/python"),
                        "-B",
                        str(stage / "scripts/probe_source_article.py"),
                        args.probe_url,
                        "--host",
                        hosts[0],
                    ],
                    cwd=stage,
                    timeout=30,
                ).splitlines()[-1]
            )
            if result.get("article_probe") != "ok":
                raise RuntimeError("source_probe_failed")
            emit(
                source_probe="ok",
                chars=result["chars"],
                text_sha256=result["text_sha256"],
            )
        emit(
            release_check="ok",
            changed_files=len(prepared),
            source_ref=manifest["source_ref"],
            real_test_sends=0,
        )
        if args.apply:
            deploy(manifest, prepared, db_path, snapshot, hosts=hosts)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else "operation_failed"
        allowed_file = re.fullmatch(
            r"(?:file_changed|new_file_already_exists):[a-zA-Z0-9_./-]+", reason
        )
        if reason not in SAFE_ERRORS and not allowed_file:
            reason = "operation_failed"
        emit(release="failed", error_type=type(exc).__name__, check=reason)
        raise SystemExit(1)
