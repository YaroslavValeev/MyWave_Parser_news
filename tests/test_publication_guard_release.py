"""Pinned release staging and failure boundaries never use a live service or database."""

import gzip
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "guard_release", ROOT / "scripts/deploy_publication_guard.py"
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
PACKAGE = ROOT / "scripts/releases/publication-guard"
RAW = (PACKAGE / "manifest.json").read_bytes()
MANIFEST = json.loads(RAW)
PAYLOAD = (PACKAGE / "payload.zip").read_bytes()


def test_artifacts_are_pinned_and_only_three_runtime_files_change():
    assert RELEASE.sha(RAW) == RELEASE.MANIFEST_SHA256
    assert RELEASE.sha(PAYLOAD) == MANIFEST["payload_sha256"]
    assert len(MANIFEST["before"]) == 106 and len(MANIFEST["after"]) == 107
    changed = {
        name
        for name, value in MANIFEST["after"].items()
        if value != MANIFEST["before"].get(name)
    }
    assert changed == set(RELEASE.WRITE_ORDER)
    assert MANIFEST["source_ref"] == "b0cae3886b30ef741a84dffbf8bd3673b540b414"
    for name, expected in MANIFEST["after"].items():
        assert RELEASE.sha(RELEASE.lf((ROOT / name).read_bytes())) == expected
    with zipfile.ZipFile(io.BytesIO(PAYLOAD)) as archive:
        assert not any(
            Path(name).name == ".env" or name.endswith((".db", ".sqlite"))
            for name in archive.namelist()
        )


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root, stage, backups = tmp_path / "live", tmp_path / "stage", tmp_path / "backups"
    root.mkdir()
    stage.mkdir()
    RELEASE.unpack(stage, MANIFEST, PAYLOAD)
    with zipfile.ZipFile(io.BytesIO(PAYLOAD)) as archive:
        archive.extractall(root)  # the pinned archive was validated by unpack above
    historic = json.loads(
        gzip.decompress(
            (
                ROOT / "tests/fixtures/manual-final-post/runtime-postimage.json.gz"
            ).read_bytes()
        )
    )
    for name, text in historic.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    (root / MANIFEST["migration"]).unlink()
    (root / ".env").write_text(
        "PRIVATE_OWNER_KEY=fixture-secret\nTEXT_MODEL=gpt-4o-mini\n", encoding="utf-8"
    )
    with sqlite3.connect(root / "data.db") as db:
        db.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT (datetime('now')))"
        )
        for version in MANIFEST["prior_migrations"]:
            db.executescript(
                (root / f"storage/migrations/{version}.sql").read_text(encoding="utf-8")
            )
            db.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            db.commit()
        db.executescript(
            """
            INSERT INTO items(id,source,content,status,checksum) VALUES
              (626,'test','owner article','discarded','626'),
              (649,'test','discarded content','discarded','649'),
              (650,'test','future post','ready_to_publish','650'),
              (7,'test','past post','published','7');
            UPDATE items SET scheduled_at='2099-10-05T06:00:00+00:00' WHERE id=650;
            INSERT INTO nlp_results(item_id,summary,author_notes) VALUES(626,'owner summary','owner note');
            INSERT INTO publications(item_id,channel_id,message_id) VALUES(7,'fixture-channel','123');
        """
        )
    monkeypatch.setattr(RELEASE, "ROOT", root)
    monkeypatch.setattr(RELEASE, "BACKUPS", backups)
    state = {
        "ActiveState": "active",
        "MainPID": "10",
        "WorkingDirectory": str(root),
        "ExecStart": str(root / "bot_aiogram.py"),
    }
    calls = []

    def service(action, *args):
        calls.append(action)
        if action == "stop":
            state.update(ActiveState="inactive", MainPID="0")
        elif action == "start":
            state.update(ActiveState="active", MainPID="20")
        return ""

    def writers(pid):
        assert int(state["MainPID"]) == pid
        return [pid] if pid else []

    monkeypatch.setattr(RELEASE, "service", service)
    monkeypatch.setattr(RELEASE, "service_state", lambda: dict(state))
    monkeypatch.setattr(RELEASE, "writers", writers)
    monkeypatch.setattr(RELEASE, "scheduled_writers", lambda: None)
    monkeypatch.setattr(RELEASE, "config_check", lambda: None)
    monkeypatch.setattr(RELEASE, "smoke", lambda stage: None)
    monkeypatch.setattr(RELEASE.time, "sleep", lambda seconds: None)
    return root, stage, backups, state, calls


def current(root, stage):
    return RELEASE.inspect_database(
        root / "data.db",
        MANIFEST,
        (stage / MANIFEST["migration"]).read_text(encoding="utf-8"),
    )


def test_check_keeps_live_bytes_and_service_unchanged(installation, capsys):
    root, stage, backups, state, calls = installation
    before = {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    RELEASE.execute(MANIFEST, PAYLOAD)
    assert before == {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    assert calls == [] and not backups.exists()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["release_check"] == "ok" and result["queue_ids"] == [650]
    assert result["production_database_writes"] == result["real_test_sends"] == 0
    assert "fixture-secret" not in json.dumps(result)


def test_deploy_preserves_owner_data_receipts_git_and_configuration(
    installation, capsys
):
    root, stage, backups, state, calls = installation
    (root / ".git").mkdir()
    (root / ".git/HEAD").write_text("independent-owner-head", encoding="utf-8")
    env = (root / ".env").read_bytes()
    before = current(root, stage)
    RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    after = current(root, stage)
    assert after["business_sha256"] == before["business_sha256"]
    assert after["protected_sha256"] == before["protected_sha256"]
    assert after["claims_present"] and after["claim_count"] == 0
    assert (root / ".env").read_bytes() == env and (
        root / ".git/HEAD"
    ).read_text() == "independent-owner-head"
    assert calls == ["stop", "start"] and state["ActiveState"] == "active"
    backup = next(backups.iterdir())
    assert json.loads((backup / "release.json").read_text())["phase"] == "committed"
    with sqlite3.connect(backup / "data.sqlite") as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='publication_claims'"
        ).fetchone()
    RELEASE.verify(root, MANIFEST["after"])
    calls.clear()
    RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    assert (
        calls == []
        and json.loads(capsys.readouterr().out.splitlines()[-1])["release"]
        == "already_installed"
    )


def test_before_activation_failure_restores_code_without_restoring_database(
    installation, monkeypatch
):
    root, stage, backups, state, calls = installation
    before = current(root, stage)
    publication = root / "services/publication.py"
    publication.write_bytes(
        RELEASE.lf(publication.read_bytes()).replace(b"\n", b"\r\n")
    )
    original = publication.read_bytes()
    monkeypatch.setattr(
        RELEASE,
        "smoke",
        lambda stage: (_ for _ in ()).throw(
            RELEASE.ReleaseError("fixture_smoke_failure")
        ),
    )
    with pytest.raises(RELEASE.ReleaseError, match="fixture_smoke_failure"):
        RELEASE.deploy(
            MANIFEST, stage, before, RELEASE.sha((root / ".env").read_bytes())
        )
    assert (root / "services/publication.py").read_bytes() == original
    assert not (root / MANIFEST["migration"]).exists()
    assert current(root, stage)["business_sha256"] == before["business_sha256"]
    assert current(root, stage)[
        "claims_present"
    ]  # additive schema retained, not a database rewind
    assert state["ActiveState"] == "active"
    assert (
        json.loads((next(backups.iterdir()) / "release.json").read_text())["phase"]
        == "restored_before_activation"
    )


def test_after_activation_failure_pauses_new_code_and_preserves_new_receipt(
    installation, monkeypatch
):
    root, stage, backups, state, calls = installation
    real_service = RELEASE.service

    def service(action, *args):
        real_service(action, *args)
        if action == "start":
            with sqlite3.connect(root / "data.db") as db:
                db.execute(
                    "INSERT INTO publications(item_id,channel_id,message_id) VALUES(650,'fixture-channel','999')"
                )
                db.execute(
                    "INSERT INTO publication_claims(item_id,token,channel_id,state,message_id,created_at,updated_at) VALUES(650,'fixture-claim','fixture-channel','delivered','999','fixture','fixture')"
                )

    monkeypatch.setattr(RELEASE, "service", service)
    with pytest.raises(
        RELEASE.ReleaseError, match="delivery_or_protected_item_changed"
    ):
        RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    assert state["ActiveState"] == "inactive"
    RELEASE.verify(root, MANIFEST["after"])
    with sqlite3.connect(root / "data.db") as db:
        assert db.execute(
            "SELECT message_id FROM publications WHERE item_id=650"
        ).fetchone() == ("999",)
    assert (
        json.loads((next(backups.iterdir()) / "release.json").read_text())["phase"]
        == "failed_paused"
    )
    with pytest.raises(RELEASE.ReleaseError, match="unfinished_guard_release"):
        RELEASE.execute(MANIFEST, PAYLOAD, apply=True)


@pytest.mark.parametrize("change", ["code", "env", "owner"])
def test_change_during_stop_does_not_overwrite_independent_work(
    installation, monkeypatch, change
):
    root, stage, backups, state, calls = installation
    real_service = RELEASE.service
    changed = False

    def service(action, *args):
        nonlocal changed
        real_service(action, *args)
        if action == "stop" and not changed:
            changed = True
            if change == "code":
                (root / "services/publication.py").write_text(
                    "owner_edit=True\n", encoding="utf-8"
                )
            elif change == "env":
                (root / ".env").write_text("owner_environment_change", encoding="utf-8")
            else:
                with sqlite3.connect(root / "data.db") as db:
                    db.execute(
                        "UPDATE nlp_results SET summary='new owner text' WHERE item_id=626"
                    )

    monkeypatch.setattr(RELEASE, "service", service)
    with pytest.raises(RELEASE.ReleaseError):
        RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    assert state["ActiveState"] == "inactive"
    if change == "code":
        assert (root / "services/publication.py").read_text() == "owner_edit=True\n"
    elif change == "env":
        assert (root / ".env").read_text() == "owner_environment_change"
    else:
        with sqlite3.connect(root / "data.db") as db:
            assert db.execute(
                "SELECT summary FROM nlp_results WHERE item_id=626"
            ).fetchone() == ("new owner text",)


def test_due_or_unscheduled_queue_blocks_without_stop(installation):
    root, stage, backups, state, calls = installation
    for schedule in (None, "bad", datetime.now(timezone.utc).isoformat()):
        with sqlite3.connect(root / "data.db") as db:
            db.execute("UPDATE items SET scheduled_at=? WHERE id=650", (schedule,))
        with pytest.raises(RELEASE.ReleaseError):
            RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
        assert calls == [] and not backups.exists()


def test_rejects_runtime_drift_and_existing_new_migration(installation):
    root, stage, backups, state, calls = installation
    (root / MANIFEST["migration"]).write_text("owner migration", encoding="utf-8")
    with pytest.raises(RELEASE.ReleaseError, match="new_migration_file_already_exists"):
        RELEASE.execute(MANIFEST, PAYLOAD)
    assert calls == []


def test_payload_tampering_and_path_traversal_are_rejected(tmp_path):
    with pytest.raises(RELEASE.ReleaseError, match="hash_mismatch"):
        RELEASE.unpack(tmp_path, MANIFEST, PAYLOAD + b"tampered")
    with pytest.raises(RELEASE.ReleaseError, match="unsafe_path"):
        RELEASE.safe_path(tmp_path, "../owner.env")


def test_unknown_migration_and_lookalike_claims_schema_fail(installation):
    root, stage, backups, state, calls = installation
    with sqlite3.connect(root / "data.db") as db:
        db.execute("INSERT INTO schema_migrations(version) VALUES('owner_migration')")
    with pytest.raises(RELEASE.ReleaseError, match="migration_versions_changed"):
        current(root, stage)
    with sqlite3.connect(root / "data.db") as db:
        db.execute("DELETE FROM schema_migrations WHERE version='owner_migration'")
        sql = (
            (stage / MANIFEST["migration"])
            .read_text(encoding="utf-8")
            .replace("UNIQUE", "")
        )
        db.executescript(sql)
    with pytest.raises(RELEASE.ReleaseError, match="claims_schema_invalid"):
        current(root, stage)


def test_process_inventory_catches_second_sender_without_printing_args(
    tmp_path, monkeypatch
):
    root = tmp_path / "live"
    root.mkdir()
    monkeypatch.setattr(RELEASE, "ROOT", root)
    proc = tmp_path / "proc"
    for pid in ("100001", "100002"):
        directory = proc / pid
        directory.mkdir(parents=True)
        (directory / "cmdline").write_bytes(
            b"python\0/fixture/bot_aiogram.py\0secret-token\0"
        )
        # resolve(strict=True) only needs an existing path in this bounded fixture.
        (directory / "cwd").mkdir()
    with pytest.raises(RELEASE.ReleaseError, match="unexpected_publication_processes"):
        RELEASE.writers(100001, proc)


def test_scheduled_writer_reference_is_rejected_without_echoing_content(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(RELEASE, "ROOT", tmp_path / "live")
    cron = tmp_path / "cron"
    cron.mkdir()
    (cron / "duplicate").write_text(
        f"* * * * * python {RELEASE.ROOT}/bot_aiogram.py secret-token\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RELEASE.ReleaseError, match="additional_scheduled_writer_reference"
    ):
        RELEASE.scheduled_writers([cron])


def test_migration_trigger_is_detected_on_copy_without_changing_live_data(installation):
    root, stage, backups, state, calls = installation
    with sqlite3.connect(root / "data.db") as db:
        db.executescript(
            "CREATE TRIGGER owner_migration_hook AFTER INSERT ON schema_migrations WHEN NEW.version='005_publication_claims' BEGIN UPDATE items SET status='approved' WHERE id=650; END;"
        )
    before = (root / "data.db").read_bytes()
    with pytest.raises(RELEASE.ReleaseError, match="migration_changes_owner_data"):
        current(root, stage)
    assert (root / "data.db").read_bytes() == before


def test_interrupted_release_journal_blocks_replay_without_service_control(
    installation,
):
    root, stage, backups, state, calls = installation
    backup = backups / "publication-guard-interrupted"
    backup.mkdir(parents=True)
    (backup / "release.json").write_text(
        '{"phase":"activation_started"}', encoding="utf-8"
    )
    with pytest.raises(RELEASE.ReleaseError, match="unfinished_guard_release"):
        RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    assert calls == []


def test_owner_edit_during_atomic_install_is_not_overwritten(installation, monkeypatch):
    root, stage, backups, state, calls = installation
    actual = RELEASE.atomic

    def atomic(path, raw, metadata=None):
        actual(path, raw, metadata)
        if (
            path == root / "services/publication.py"
            and RELEASE.sha(RELEASE.lf(raw))
            == MANIFEST["after"]["services/publication.py"]
        ):
            (root / "storage/repository.py").write_text(
                "owner_repository_edit=True\n", encoding="utf-8"
            )

    monkeypatch.setattr(RELEASE, "atomic", atomic)
    with pytest.raises(RELEASE.ReleaseError, match="independent_code_change"):
        RELEASE.execute(MANIFEST, PAYLOAD, apply=True)
    assert (
        root / "storage/repository.py"
    ).read_text() == "owner_repository_edit=True\n"
    assert state["ActiveState"] == "inactive"


def test_isolated_payload_workflow_uses_fake_transports_and_no_live_environment(
    tmp_path, monkeypatch
):
    stage = tmp_path / "stage"
    stage.mkdir()
    RELEASE.unpack(stage, MANIFEST, PAYLOAD)
    actual = RELEASE.command

    def command(args, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "TELEGRAM_BOT_TOKEN" not in kwargs["env"]
        return actual([sys.executable, *args[1:]], **kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-live-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-live-secret")
    monkeypatch.setattr(RELEASE, "command", command)
    RELEASE.smoke(stage)
