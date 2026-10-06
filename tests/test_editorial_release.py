import difflib
import importlib.util
import json
from pathlib import Path
import sqlite3

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
