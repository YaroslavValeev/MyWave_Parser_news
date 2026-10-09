import difflib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy_editorial_release.py"
SPEC = importlib.util.spec_from_file_location("editorial_release", SCRIPT)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def setup(root):
    root.mkdir()
    old = b"value = 1\n"
    new = b"value = 2\n"
    (root / "old.py").write_bytes(old.replace(b"\n", b"\r\n"))
    (root / "guard.py").write_bytes(b"guard = 1\n")
    (root / ".env").write_text(
        "TEXT_MODEL=gpt-4o-mini\nOTHER_SETTING=keep\n", encoding="utf-8"
    )
    manifest = {
        "before": {"old.py": RELEASE.digest(old)},
        "after": {
            "old.py": RELEASE.digest(new),
            "new.py": RELEASE.digest(b"added = 1\n"),
        },
        "guard": {"guard.py": RELEASE.digest(b"guard = 1\n")},
    }
    patch = "".join(
        difflib.unified_diff(
            old.decode().splitlines(True),
            new.decode().splitlines(True),
            fromfile="a/old.py",
            tofile="b/old.py",
        )
    )
    patch += "".join(
        difflib.unified_diff(
            [], ["added = 1\n"], fromfile="/dev/null", tofile="b/new.py"
        )
    )
    return manifest, patch.encode()


def database(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY,status TEXT,content TEXT);"
            "CREATE TABLE nlp_results(item_id INTEGER PRIMARY KEY,summary TEXT);"
            "CREATE TABLE publications(item_id INTEGER);"
            "INSERT INTO items VALUES(649,'discarded','source');"
            "INSERT INTO nlp_results VALUES(649,'safe redaction');"
        )


def test_staging_preserves_live_files_and_rejects_drift(tmp_path):
    root, stage = tmp_path / "server", tmp_path / "stage"
    manifest, patch = setup(root)
    stage.mkdir()
    original = (root / "old.py").read_bytes()
    prepared = RELEASE.prepare(root, stage, manifest, patch)
    assert prepared["old.py"] == b"value = 2\n" and prepared["new.py"] == b"added = 1\n"
    assert (root / "old.py").read_bytes() == original and not (root / "new.py").exists()
    (root / "old.py").write_bytes(b"operator_edit = 1\n")
    with pytest.raises(RuntimeError, match="file_changed:old.py"):
        RELEASE.prepare(root, stage, manifest, patch)
    assert (root / "old.py").read_bytes() == b"operator_edit = 1\n"


@pytest.mark.parametrize("failure", [None, "install", "smoke", "health"])
def test_deploy_and_recovery_preserve_database_owner_code_and_environment(
    tmp_path, monkeypatch, failure
):
    root, stage = tmp_path / "server", tmp_path / "stage"
    manifest, patch = setup(root)
    stage.mkdir()
    prepared = RELEASE.prepare(root, stage, manifest, patch)
    db_path = root / "data.db"
    database(db_path)
    snapshot = RELEASE.target_snapshot(db_path)
    original = (root / "old.py").read_bytes()
    monkeypatch.setattr(RELEASE, "ROOT", root)
    monkeypatch.setattr(RELEASE, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(RELEASE.time, "sleep", lambda seconds: None)
    calls = []
    monkeypatch.setattr(
        RELEASE,
        "service",
        lambda *args: calls.append(args) or ("777" if args[0] == "show" else ""),
    )
    install = RELEASE.install
    installation_count = 0

    def install_once(path, raw, metadata=None):
        nonlocal installation_count
        installation_count += 1
        if failure == "install" and installation_count == 2:
            raise OSError("simulated disk failure")
        return install(path, raw, metadata)

    monkeypatch.setattr(RELEASE, "install", install_once)

    def migrate(*args, **kwargs):
        with sqlite3.connect(db_path) as db:
            db.execute("ALTER TABLE items ADD COLUMN source_context TEXT")
        return ""

    monkeypatch.setattr(RELEASE, "run", migrate)
    monkeypatch.setattr(
        RELEASE,
        "smoke",
        lambda root: (
            (_ for _ in ()).throw(RuntimeError("offline_smoke_failed"))
            if failure == "smoke"
            else None
        ),
    )

    def health():
        if failure == "health":
            with (root / ".env").open("a", encoding="utf-8") as handle:
                handle.write("INDEPENDENT_SETTING=new\n")
            raise RuntimeError("service_unstable")
        return "777"

    monkeypatch.setattr(RELEASE, "health", health)
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            RELEASE.deploy(
                manifest, prepared, db_path, snapshot, hosts=("article.example.test",)
            )
        assert (root / "old.py").read_bytes() == original and not (
            root / "new.py"
        ).exists()
        assert "SOURCE_ARTICLE_FETCH_ENABLED" not in (root / ".env").read_text()
        if failure == "health":
            assert "INDEPENDENT_SETTING=new" in (root / ".env").read_text()
    else:
        RELEASE.deploy(
            manifest, prepared, db_path, snapshot, hosts=("article.example.test",)
        )
        assert (root / "old.py").read_bytes() == b"value = 2\r\n"
        assert (root / "new.py").read_bytes() == b"added = 1\n"
        backup = next((tmp_path / "backups").iterdir())
        assert (backup / "data.sqlite").is_file() and (
            backup / "manifest.json"
        ).is_file()
        RELEASE.rollback(str(backup))
        assert (root / "old.py").read_bytes() == original and not (
            root / "new.py"
        ).exists()
    assert RELEASE.target_snapshot(db_path) == snapshot
    assert "OTHER_SETTING=keep" in (root / ".env").read_text()
    assert calls[-1] == ("start",)


def test_manifest_binds_artifacts_and_overlay_keeps_server_features():
    directory = SCRIPT.parent / "releases/editorial-4o-mini"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert (
        RELEASE.digest((directory / "manifest.json").read_bytes())
        == RELEASE.MANIFEST_SHA256
    )
    assert (
        RELEASE.digest((directory / "overlay.patch").read_bytes())
        == manifest["patch_sha256"]
    )
    patch = (directory / "overlay.patch").read_text(encoding="utf-8")
    deleted = [
        line
        for line in patch.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert not any(
        "MEDIA_HYDRATE" in line
        or "MAINTENANCE" in line
        or "COLLECT_CONTACTS" in line
        or "AuthenticationError" in line
        for line in deleted
    )


def test_path_and_rollback_cannot_escape_workspace(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(RELEASE, "BACKUPS", root)
    with pytest.raises(RuntimeError, match="unsafe_release_path"):
        RELEASE.path_for(root, "../outside.py")
    with pytest.raises(RuntimeError, match="unsafe_backup_path"):
        RELEASE.rollback(str(root.parent))


def test_offline_smoke_does_not_enter_sheets_or_media_with_enabled_configuration():
    code = """import asyncio,json,runpy,sys
namespace=runpy.run_path(sys.argv[1],run_name="offline_smoke_test")
from config.settings import config
from services import raw_feed_sync
from telegram_bot import views
config.GOOGLE_SHEET_ID="offline-sheet"
config.GOOGLE_CREDENTIALS_FILE="offline-credentials.json"
calls=[]
async def sheets(*args,**kwargs):
 calls.append("sheets")
 return None
async def media(*args,**kwargs):
 calls.append("media")
 return None
raw_feed_sync._get_doc=sheets
views.maybe_autoupload_local_cover_and_sync_sheet=media
asyncio.run(asyncio.wait_for(namespace["main"](),timeout=25))
print(json.dumps({"external_hooks":calls}))
"""
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            code,
            str(SCRIPT.parent / "editorial_offline_smoke.py"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert process.returncode == 0, process.stderr[-1000:]
    assert json.loads(process.stdout.splitlines()[-1]) == {"external_hooks": []}


def test_timeout_diagnostics_only_show_whitelisted_progress(
    monkeypatch, capsys, tmp_path
):
    output = b'private output\n{"smoke_phase":"owner_comment","source_changed":false,"key":"private-key"}\n'

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            ["private-command"], 60, output=output, stderr=b"private stderr"
        )

    monkeypatch.setattr(RELEASE.subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        RELEASE.run(["private-command"], cwd=tmp_path)
    line = capsys.readouterr().out
    assert json.loads(line) == {
        "subprocess_check": "timeout",
        "timeout_seconds": 60,
        "child_progress": {"smoke_phase": "owner_comment", "source_changed": False},
    }
    assert "private" not in line


def test_child_progress_ignores_arbitrary_output_and_malformed_fields():
    assert (
        RELEASE.child_progress('{"smoke_phase":["private"]}\n{"secret":"private"}\n')
        is None
    )


def test_article_link_package_is_an_update_of_the_installed_release():
    directory = SCRIPT.parent / "releases/article-links"
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    original = json.loads(
        (SCRIPT.parent / "releases/editorial-4o-mini/manifest.json").read_bytes()
    )
    assert RELEASE.digest(raw) == RELEASE.PACKAGE_MANIFESTS["article-links"]
    assert (
        set(manifest["before"])
        == set(manifest["after"])
        == {
            "utils/source_context.py",
            "utils/item_context.py",
            "scripts/editorial_offline_smoke.py",
        }
    )
    assert all(v == original["after"][name] for name, v in manifest["before"].items())
    assert (
        RELEASE.digest((directory / "overlay.patch").read_bytes())
        == manifest["patch_sha256"]
    )


def test_summary_package_preserves_installed_profile_and_limits_changed_files():
    directory = SCRIPT.parent / "releases/summary-conditions"
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    prior = json.loads(
        (SCRIPT.parent / "releases/article-links/manifest.json").read_bytes()
    )
    expected = {**prior["guard"], **prior["after"]}
    target = "nlp/openai_client.py"
    assert RELEASE.digest(raw) == RELEASE.PACKAGE_MANIFESTS["summary-conditions"]
    assert set(manifest["before"]) == set(manifest["after"]) == {target}
    assert manifest["before"] == {target: expected.pop(target)}
    assert manifest["guard"] == expected
    patch = (directory / "overlay.patch").read_bytes()
    assert RELEASE.digest(patch) == manifest["patch_sha256"]
    assert patch.count(b"diff --git") == 1
    assert b"a/nlp/openai_client.py b/nlp/openai_client.py" in patch


def test_alignment_package_matches_audited_seven_files_and_guards_all_others():
    from scripts.audit_reconciled_runtime import EXPECTED

    directory = SCRIPT.parent / "releases/runtime-alignment"
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    assert RELEASE.digest(raw) == RELEASE.PACKAGE_MANIFESTS["runtime-alignment"]
    assert (
        RELEASE.digest((directory / "overlay.patch").read_bytes())
        == manifest["patch_sha256"]
    )
    assert len(manifest["after"]) == 7 and manifest["protect_item_626"] is True
    assert manifest["initialize_database"] is False
    assert {**manifest["guard"], **manifest["after"]}.items() >= EXPECTED.items()
    assert set(manifest["before"]) == set(manifest["after"])


def test_alignment_patch_recreates_audited_before_and_applies_forward(tmp_path):
    root, stage = tmp_path / "server", tmp_path / "stage"
    root.mkdir()
    stage.mkdir()
    repository = SCRIPT.parents[1]
    directory = SCRIPT.parent / "releases/runtime-alignment"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    patch = (directory / "overlay.patch").read_bytes()
    # This historical patch's EOF handling is independent of newer runtime files.
    for name in manifest["after"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(RELEASE.lf((repository / name).read_bytes()))
    patch_path = tmp_path / "overlay.patch"
    patch_path.write_bytes(patch)
    RELEASE.run(["git", "apply", "--reverse", str(patch_path)], cwd=root)
    RELEASE.verify(root, manifest["before"])
    before = {name: (root / name).read_bytes() for name in manifest["before"]}
    prepared = RELEASE.prepare(root, stage, {**manifest, "guard": {}}, patch)
    assert set(prepared) == set(manifest["after"])
    assert all((root / name).read_bytes() == raw for name, raw in before.items())
    RELEASE.verify(stage, manifest["after"])


def add_review_item(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO items VALUES(626,'review','bound source')")
        db.execute("INSERT INTO nlp_results VALUES(626,'reviewed summary')")


def test_release_snapshot_preserves_626_and_rejects_status_or_publication_change(
    tmp_path,
):
    db_path = tmp_path / "data.db"
    database(db_path)
    add_review_item(db_path)
    manifest = {"protect_item_626": True}
    before = db_path.read_bytes()
    value = RELEASE.release_snapshot(db_path, manifest)
    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE nlp_results SET summary='owner update' WHERE item_id=626")
    assert RELEASE.release_snapshot(db_path, manifest) != value
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE items SET status='discarded' WHERE id=626")
    with pytest.raises(RuntimeError, match="target_owner_state_changed"):
        RELEASE.release_snapshot(db_path, manifest)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE items SET status='review' WHERE id=626")
        db.execute("INSERT INTO publications VALUES(626)")
    with pytest.raises(RuntimeError, match="target_owner_state_changed"):
        RELEASE.release_snapshot(db_path, manifest)


def test_alignment_deploy_changes_no_database_records_and_skips_migration(
    tmp_path, monkeypatch
):
    root, stage = tmp_path / "server", tmp_path / "stage"
    manifest, patch = setup(root)
    manifest.update(protect_item_626=True, initialize_database=False)
    stage.mkdir()
    prepared = RELEASE.prepare(root, stage, manifest, patch)
    db_path = root / "data.db"
    database(db_path)
    add_review_item(db_path)
    snapshot = RELEASE.release_snapshot(db_path, manifest)
    before = db_path.read_bytes()
    monkeypatch.setattr(RELEASE, "ROOT", root)
    monkeypatch.setattr(RELEASE, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(RELEASE, "service", lambda *args: "777")
    monkeypatch.setattr(RELEASE, "health", lambda: "777")
    monkeypatch.setattr(RELEASE, "smoke", lambda root: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("migration or external command must not run")

    monkeypatch.setattr(RELEASE, "run", forbidden)
    RELEASE.deploy(manifest, prepared, db_path, snapshot)
    assert (
        db_path.read_bytes() == before
        and RELEASE.release_snapshot(db_path, manifest) == snapshot
    )
    backup = next((tmp_path / "backups").iterdir())
    RELEASE.rollback(str(backup))
    assert db_path.read_bytes() == before


def test_alignment_detects_owner_change_during_service_stop_before_code_writes(
    tmp_path, monkeypatch
):
    root, stage = tmp_path / "server", tmp_path / "stage"
    manifest, patch = setup(root)
    manifest.update(protect_item_626=True, initialize_database=False)
    stage.mkdir()
    prepared = RELEASE.prepare(root, stage, manifest, patch)
    db_path = root / "data.db"
    database(db_path)
    add_review_item(db_path)
    snapshot = RELEASE.release_snapshot(db_path, manifest)
    before = (root / "old.py").read_bytes()
    monkeypatch.setattr(RELEASE, "ROOT", root)
    monkeypatch.setattr(RELEASE, "BACKUPS", tmp_path / "backups")
    changed = False

    def service(*args):
        nonlocal changed
        if args[0] == "stop" and not changed:
            changed = True
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "UPDATE nlp_results SET summary='owner update' WHERE item_id=626"
                )
        return "777"

    monkeypatch.setattr(RELEASE, "service", service)
    with pytest.raises(RuntimeError, match="target_owner_state_changed"):
        RELEASE.deploy(manifest, prepared, db_path, snapshot)
    assert (root / "old.py").read_bytes() == before
