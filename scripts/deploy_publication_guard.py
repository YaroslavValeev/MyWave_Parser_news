"""Pinned publication-guard release. Default is read-only staging, never activation."""

from __future__ import annotations

import argparse
import ast
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile

ROOT = Path("/opt/bot3/parser-new-bot")
BACKUPS = Path("/opt/bot3/backups")
SERVICE = "parser-news-bot"
MANIFEST_SHA256 = "38b4b001f0b3cec1a4015e136574f06dda72f6db0be869e1ee3ba7747770fad1"
WRITE_ORDER = [
    "services/publication.py",
    "storage/repository.py",
    "storage/migrations/005_publication_claims.sql",
]
VERSION = "005_publication_claims"


class ReleaseError(RuntimeError):
    pass


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def lf(raw):
    return raw.replace(b"\r\n", b"\n")


def safe_path(root, name):
    relative = Path(name)
    target = root / relative
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not target.resolve().is_relative_to(root.resolve())
    ):
        raise ReleaseError("unsafe_path")
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ReleaseError("unsafe_symlink")
    return target


def verify(root, expected):
    for name, value in expected.items():
        path = safe_path(root, name)
        if not path.is_file() or sha(lf(path.read_bytes())) != value:
            raise ReleaseError("runtime_file_changed:" + name)


def fetch(url, expected):
    with urlopen(url, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or sha(raw) != expected:
        raise ReleaseError("release_artifact_hash_mismatch")
    return raw


def unpack(stage, manifest, payload):
    if sha(payload) != manifest["payload_sha256"]:
        raise ReleaseError("release_artifact_hash_mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(
            manifest["payload_files"]
        ):
            raise ReleaseError("payload_file_list_changed")
        if sum(info.file_size for info in archive.infolist()) > 20 * 1024 * 1024:
            raise ReleaseError("payload_too_large")
        for info in archive.infolist():
            target = safe_path(stage, info.filename)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ReleaseError("unsafe_payload_member")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    verify(stage, manifest["after"])
    if (
        sha((stage / "scripts/editorial_offline_smoke.py").read_bytes())
        != manifest["smoke_sha256"]
    ):
        raise ReleaseError("smoke_script_changed")
    for path in stage.rglob("*.py"):
        ast.parse(
            path.read_text(encoding="utf-8"), filename=str(path.relative_to(stage))
        )


def command(args, **kwargs):
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=240, **kwargs
    )
    if result.returncode:
        raise ReleaseError("child_command_failed")
    return result.stdout.strip()


def service(*args):
    return command(["systemctl", *args, SERVICE, "--no-pager"])


def service_state():
    result = {}
    for line in service(
        "show", "--property=MainPID,ActiveState,WorkingDirectory,ExecStart"
    ).splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    if result.get("WorkingDirectory") != str(ROOT):
        raise ReleaseError("service_directory_changed")
    if str(ROOT / "bot_aiogram.py") not in result.get("ExecStart", ""):
        raise ReleaseError("service_entrypoint_changed")
    return result


def writers(expected_pid, proc=Path("/proc")):
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            args = [value.decode("utf-8", errors="replace") for value in args if value]
            if not args:
                continue
            cwd = (entry / "cwd").resolve(strict=True)
            python = "python" in Path(args[0]).name
            relevant = any(Path(value).name == "bot_aiogram.py" for value in args)
            relevant |= python and (
                cwd == ROOT or any(str(ROOT) in value for value in args)
            )
            if relevant:
                found.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise ReleaseError("process_inventory_incomplete") from None
    if sorted(found) != ([int(expected_pid)] if int(expected_pid) else []):
        raise ReleaseError("unexpected_publication_processes")
    return found


def scheduled_writers(paths=None):
    paths = paths or [
        Path("/etc/cron.d"),
        Path("/etc/crontab"),
        Path("/var/spool/cron"),
        Path("/etc/systemd/system"),
        Path("/lib/systemd/system"),
    ]
    for directory in paths:
        files = directory.rglob("*") if directory.is_dir() else [directory]
        for path in files:
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            if "systemd" in path.parts:
                if path.suffix not in {".service", ".timer", ".conf"}:
                    continue
                if (
                    path.name == SERVICE + ".service"
                    or SERVICE + ".service.d" in path.parts
                ):
                    continue
            raw = path.read_bytes()
            if str(ROOT).encode() in raw or b"bot_aiogram.py" in raw:
                raise ReleaseError("additional_scheduled_writer_reference")


def readonly(path):
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("unsafe_or_missing_database")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.execute("PRAGMA query_only=ON")
    return db


def snapshot(db, ids):
    tables = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if not row[0].startswith("sqlite_")
    ]
    digest = hashlib.sha256()
    for table in tables:
        if table in {"schema_migrations", "publication_claims"}:
            continue
        name = '"' + table.replace('"', '""') + '"'
        digest.update(table.encode())
        for row in db.execute("SELECT * FROM " + name + " ORDER BY rowid"):
            digest.update(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=lambda value: value.hex(),
                ).encode()
            )
            digest.update(b"\n")
    protected = []
    for item in ids:
        for table, key in (
            ("items", "id"),
            ("nlp_results", "item_id"),
            ("publications", "item_id"),
        ):
            protected.append(
                list(
                    db.execute(
                        f"SELECT * FROM {table} WHERE {key}=? ORDER BY rowid", (item,)
                    )
                )
            )
    queued = list(
        db.execute(
            "SELECT id,scheduled_at FROM items WHERE status IN ('approved','ready_to_publish','publish_retry') ORDER BY id"
        )
    )
    versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    claims = (
        db.execute("SELECT count(*) FROM publication_claims").fetchone()[0]
        if "publication_claims" in tables
        else 0
    )
    return {
        "business_sha256": digest.hexdigest(),
        "protected_sha256": sha(json.dumps(protected, default=str).encode()),
        "queue": queued,
        "versions": sorted(versions),
        "claim_count": claims,
        "claims_present": "publication_claims" in tables,
    }


def assert_future_queue(value, now=None):
    deadline = (now or datetime.now(timezone.utc)) + timedelta(minutes=15)
    for _, raw in value["queue"]:
        try:
            when = datetime.fromisoformat(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            raise ReleaseError("queued_item_has_no_safe_future_schedule") from None
        if when <= deadline:
            raise ReleaseError("publication_due_during_rollout")


def migrate(db, sql, prior):
    versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    if versions not in (set(prior), {*prior, VERSION}):
        raise ReleaseError("migration_versions_changed")
    if VERSION not in versions:
        db.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + "\nINSERT INTO schema_migrations(version) VALUES ('005_publication_claims');\nCOMMIT;"
        )
    columns = [
        (row[1], row[2], row[5])
        for row in db.execute("PRAGMA table_info(publication_claims)")
    ]
    expected = [
        ("item_id", "INTEGER", 1),
        ("token", "TEXT", 0),
        ("channel_id", "TEXT", 0),
        ("state", "TEXT", 0),
        ("message_id", "TEXT", 0),
        ("created_at", "TEXT", 0),
        ("updated_at", "TEXT", 0),
    ]
    recorded_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='publication_claims'"
    ).fetchone()
    normalize = (
        lambda value: "".join(value.upper().split())
        .replace("IFNOTEXISTS", "")
        .rstrip(";")
    )
    if (
        columns != expected
        or not recorded_sql
        or normalize(recorded_sql[0]) != normalize(sql)
        or db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
    ):
        raise ReleaseError("claims_schema_invalid")


def inspect_database(path, manifest, sql, *, simulate=True):
    with closing(readonly(path)) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ReleaseError("database_integrity_failed")
        if (
            source.execute("PRAGMA page_count").fetchone()[0]
            * source.execute("PRAGMA page_size").fetchone()[0]
            > 256 * 1024 * 1024
        ):
            raise ReleaseError("database_exceeds_staging_budget")
        with closing(sqlite3.connect(":memory:")) as copied:
            source.backup(copied)
            before = snapshot(copied, manifest["protected_item_ids"])
            if simulate:
                migrate(copied, sql, manifest["prior_migrations"])
                after = snapshot(copied, manifest["protected_item_ids"])
                if (
                    before["business_sha256"] != after["business_sha256"]
                    or before["protected_sha256"] != after["protected_sha256"]
                ):
                    raise ReleaseError("migration_changes_owner_data")
            return before


def smoke(stage):
    # No live environment/credentials or transport, including during imports.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}
    }
    env.update(
        PYTHONPATH=str(stage), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"
    )
    code = 'import runpy,sys;from unittest.mock import patch;\nwith patch("socket.socket.connect",side_effect=RuntimeError("offline_network_forbidden")),patch("socket.socket.connect_ex",side_effect=RuntimeError("offline_network_forbidden")),patch("socket.getaddrinfo",side_effect=RuntimeError("offline_dns_forbidden")),patch("socket.socket.sendto",side_effect=RuntimeError("offline_udp_forbidden")):\n runpy.run_path(sys.argv[1],run_name="__main__")'
    output = command(
        [
            str(ROOT / "venv/bin/python"),
            "-B",
            "-c",
            code,
            str(stage / "scripts/editorial_offline_smoke.py"),
        ],
        cwd=stage,
        env=env,
    )
    expected = {
        "offline_smoke": "ok",
        "cases": 2,
        "real_telegram_sends": 0,
        "real_openai_requests": 0,
    }
    if json.loads(output.splitlines()[-1]) != expected:
        raise ReleaseError("offline_smoke_failed")


def config_check():
    code = 'import json,logging;logging.disable(logging.CRITICAL);from config.settings import config;print(json.dumps({"model":config.TEXT_MODEL,"db":str(config.DB_PATH)}))'
    value = json.loads(
        command(
            [str(ROOT / "venv/bin/python"), "-B", "-c", code], cwd=ROOT
        ).splitlines()[-1]
    )
    if (
        value["model"] != "gpt-4o-mini"
        or Path(value["db"]).resolve() != ROOT / "data.db"
    ):
        raise ReleaseError("model_or_database_configuration_changed")


def atomic(path, raw, metadata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".publication-guard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777 if metadata else 0o600)
        if metadata and hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def journal(backup, phase, **details):
    atomic(
        backup / "release.json",
        (json.dumps({"phase": phase, **details}, indent=2) + "\n").encode(),
    )


def pending_releases():
    for directory in BACKUPS.glob("publication-guard-*"):
        if directory.is_symlink():
            raise ReleaseError("unsafe_backup_directory")
        record = directory / "release.json"
        if not record.is_file() or json.loads(record.read_text(encoding="utf-8")).get(
            "phase"
        ) not in {"committed", "restored_before_activation"}:
            raise ReleaseError("unfinished_guard_release")


def stop_safely():
    try:
        service("stop")
        return service_state().get("ActiveState") == "inactive"
    except Exception:
        return False


def deploy(manifest, stage, before, env_hash):
    db_path = ROOT / "data.db"
    sql = (stage / manifest["migration"]).read_text(encoding="utf-8")
    pending_releases()
    verify(ROOT, manifest["before"])
    if safe_path(ROOT, manifest["migration"]).exists():
        raise ReleaseError("new_migration_file_already_exists")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if BACKUPS.is_symlink():
        raise ReleaseError("unsafe_backup_directory")
    backup = BACKUPS / (
        "publication-guard-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    backup.mkdir(mode=0o700)
    originals = {}
    for name in WRITE_ORDER:
        path = safe_path(ROOT, name)
        originals[name] = (
            (path.read_bytes(), path.stat()) if path.exists() else (None, None)
        )
        if path.exists():
            atomic(backup / "code" / name, path.read_bytes())
    details = {
        "source_ref": manifest["source_ref"],
        "root": str(ROOT),
        "before": before,
        "env_sha256": env_hash,
        "original_files": {
            name: {
                "present": raw is not None,
                "sha256": sha(raw) if raw is not None else None,
                "mode": metadata.st_mode & 0o777 if metadata else None,
                "uid": metadata.st_uid if metadata else None,
                "gid": metadata.st_gid if metadata else None,
            }
            for name, (raw, metadata) in originals.items()
        },
    }
    journal(backup, "prepared", **details)
    emit(release_phase="backup_prepared", backup=str(backup))
    stopped = activated = False
    try:
        service("stop")
        stopped = True
        if service_state()["ActiveState"] != "inactive":
            raise ReleaseError("service_did_not_stop")
        writers(0)
        scheduled_writers()
        verify(ROOT, manifest["before"])
        if safe_path(ROOT, manifest["migration"]).exists():
            raise ReleaseError("new_migration_file_already_exists")
        current = inspect_database(db_path, manifest, sql)
        if current != before or sha((ROOT / ".env").read_bytes()) != env_hash:
            raise ReleaseError("owner_or_configuration_changed_before_install")
        assert_future_queue(current)
        with closing(readonly(db_path)) as source, closing(
            sqlite3.connect(backup / "data.sqlite")
        ) as target:
            source.backup(target)
        os.chmod(backup / "data.sqlite", 0o600)
        journal(backup, "installing", **details)
        # New service first: a reboot during a partial install fails before Telegram.
        for name in WRITE_ORDER:
            old, metadata = originals[name]
            path = safe_path(ROOT, name)
            if (path.read_bytes() if path.exists() else None) != old:
                raise ReleaseError("independent_code_change")
            if metadata is None:
                metadata = safe_path(
                    ROOT, "storage/migrations/004_source_context.sql"
                ).stat()
            raw = (stage / name).read_bytes()
            if old is not None and b"\r\n" in old:
                raw = lf(raw).replace(b"\n", b"\r\n")
            atomic(ROOT / name, raw, metadata)
        with closing(sqlite3.connect(db_path)) as db:
            migrate(db, sql, manifest["prior_migrations"])
        migrated = inspect_database(db_path, manifest, sql)
        if (
            migrated["business_sha256"] != before["business_sha256"]
            or migrated["claim_count"]
        ):
            raise ReleaseError("database_changed_before_activation")
        verify(ROOT, manifest["after"])
        smoke(stage)
        if sha((ROOT / ".env").read_bytes()) != env_hash:
            raise ReleaseError("configuration_changed")
        journal(backup, "activation_started", **details)
        activated = True
        service("start")
        state = service_state()
        time.sleep(5)
        later = service_state()
        if (
            state.get("ActiveState") != "active"
            or not int(state.get("MainPID", 0))
            or later.get("MainPID") != state.get("MainPID")
            or later.get("ActiveState") != "active"
        ):
            raise ReleaseError("service_health_failed")
        writers(int(state["MainPID"]))
        observed = inspect_database(db_path, manifest, sql)
        if (
            observed["protected_sha256"] != before["protected_sha256"]
            or observed["claim_count"]
        ):
            raise ReleaseError("delivery_or_protected_item_changed_during_activation")
        verify(ROOT, manifest["after"])
        journal(backup, "committed", main_pid=state["MainPID"], **details)
        emit(
            release="ok",
            backup=str(backup),
            main_pid=state["MainPID"],
            text_model="gpt-4o-mini",
            real_test_sends=0,
            database_restored=False,
        )
    except BaseException:
        stopped_confirmed = stop_safely() if stopped else False
        restored = False
        if stopped_confirmed and not activated:
            try:
                writers(0)
                current = inspect_database(db_path, manifest, sql)
                if (
                    current["business_sha256"] != before["business_sha256"]
                    or current["claim_count"]
                    or sha((ROOT / ".env").read_bytes()) != env_hash
                ):
                    raise ReleaseError("unsafe_pre_activation_restore")
                for name in reversed(WRITE_ORDER):
                    path = safe_path(ROOT, name)
                    old, metadata = originals[name]
                    if path.exists() and sha(lf(path.read_bytes())) not in {
                        manifest["after"][name],
                        manifest["before"].get(name),
                    }:
                        raise ReleaseError("independent_code_change")
                    if old is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, old, metadata)
                verify(ROOT, manifest["before"])
                service("start")
                restored = True
            except Exception:
                stopped_confirmed = stop_safely()
        journal(
            backup,
            "restored_before_activation" if restored else "failed_paused",
            **details,
        )
        emit(
            release="restored_before_activation" if restored else "failed_paused",
            backup=str(backup),
            database_restored=False,
            service_left_stopped=stopped_confirmed and not restored,
        )
        raise


def execute(manifest, payload, *, apply=False):
    pending_releases()
    state = service_state()
    if state.get("ActiveState") != "active" or not int(state.get("MainPID", 0)):
        raise ReleaseError("service_not_active")
    writers(int(state["MainPID"]))
    scheduled_writers()
    installed = all(
        safe_path(ROOT, name).is_file()
        and sha(lf(safe_path(ROOT, name).read_bytes())) == value
        for name, value in manifest["after"].items()
    )
    verify(ROOT, manifest["after"] if installed else manifest["before"])
    if not installed and (ROOT / manifest["migration"]).exists():
        raise ReleaseError("new_migration_file_already_exists")
    config_check()
    with tempfile.TemporaryDirectory(prefix="mywave-guard-stage-") as directory:
        stage = Path(directory)
        unpack(stage, manifest, payload)
        emit(release_phase="offline_workflow")
        smoke(stage)
        sql = (stage / manifest["migration"]).read_text(encoding="utf-8")
        before = inspect_database(ROOT / "data.db", manifest, sql)
        if installed:
            if not before["claims_present"] or VERSION not in before["versions"]:
                raise ReleaseError("installed_code_without_migration")
            emit(
                release="already_installed",
                matched_files=len(manifest["after"]),
                database_writes=0,
                telegram_sends=0,
            )
            return
        if before["claim_count"]:
            raise ReleaseError("unresolved_guard_claims")
        assert_future_queue(before)
        verify(ROOT, manifest["before"])
        config_check()
        env_hash = sha(safe_path(ROOT, ".env").read_bytes())
        if not apply:
            emit(
                release_check="ok",
                source_ref=manifest["source_ref"],
                matched_files=len(manifest["before"]),
                changed_files=len(WRITE_ORDER),
                queue_ids=[row[0] for row in before["queue"]],
                migration_tested_in_memory=True,
                production_database_writes=0,
                real_test_sends=0,
            )
            return

        deploy(manifest, stage, before, env_hash)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--package-base", required=True)
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0 or Path.cwd().resolve() != ROOT:
        raise ReleaseError("run_as_root_in_project")
    os.umask(0o077)
    url = urlsplit(args.package_base)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ReleaseError("invalid_package_base")
    raw = fetch(args.package_base.rstrip("/") + "/manifest.json", MANIFEST_SHA256)
    manifest = json.loads(raw)
    if (
        manifest["schema_version"] != 1
        or manifest["write_order"] != WRITE_ORDER
        or manifest["migration_version"] != VERSION
    ):
        raise ReleaseError("invalid_release_manifest")
    payload = fetch(
        args.package_base.rstrip("/") + "/payload.zip", manifest["payload_sha256"]
    )
    import fcntl

    fd = os.open(
        "/run/lock/mywave-publication-guard.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.apply:

            def interrupted(signum, frame):
                raise InterruptedError("deployment_interrupted")

            for signum in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(signum, interrupted)
        execute(manifest, payload, apply=args.apply)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        reason = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        emit(release="failed", reason=reason)
        raise SystemExit(1)
