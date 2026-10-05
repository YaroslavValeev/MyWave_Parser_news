import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/apply_source_context_hotfix.py"
SPEC = importlib.util.spec_from_file_location("source_context_hotfix", SCRIPT)
HOTFIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOTFIX)


def test_patch_staging_preserves_crlf_and_original_files(tmp_path):
    root = tmp_path / "server"
    staging = tmp_path / "stage"
    staging.mkdir()
    chunks = []
    originals = {}
    for name in HOTFIX.FILES:
        label = 'LABEL = "<b>Саммари (NLP)</b>"\n' if name.endswith("views.py") else ""
        old = label + "value = 1\n"
        new = label + "value = 2\n"
        chunks.append(
            "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile="a/" + name,
                    tofile="b/" + name,
                )
            )
        )
        deployed = old.replace("Саммари (NLP)", "Кратко (факты)").encode()
        if name != "utils/item_context.py":
            deployed = deployed.replace(b"\n", b"\r\n")
        originals[name] = deployed
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(deployed)
    prepared = HOTFIX.prepare_code(root, staging, "".join(chunks).encode())
    for name, raw in prepared.items():
        assert raw == originals[name].replace(b"value = 1", b"value = 2")
        assert (root / name).read_bytes() == originals[name]
        HOTFIX.install_file(root / name, raw)
        assert (root / name).read_bytes() == raw
        HOTFIX.install_file(root / name, originals[name])
        assert (root / name).read_bytes() == originals[name]


def test_changed_server_file_is_rejected_before_writes(tmp_path):
    file = tmp_path / "existing.py"
    file.write_bytes(b"edited = True\n")
    with pytest.raises(RuntimeError, match="server_file_changed"):
        HOTFIX.verify_files(tmp_path, {"existing.py": HOTFIX.blob(b"edited = False\n")})
    assert file.read_bytes() == b"edited = True\n"


def make_database(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, content TEXT, transcript TEXT, status TEXT);"
            "CREATE TABLE nlp_results(item_id INTEGER PRIMARY KEY, summary TEXT, questions TEXT, "
            "decision TEXT, extra TEXT, merged_text TEXT, author_notes TEXT, voice_file TEXT, "
            "updated_at TEXT, version INTEGER);"
        )
        db.executemany(
            "INSERT INTO items VALUES(?,?,?,?)",
            [
                (649, "https://wakeflot.ru/news/1785", "", "review"),
                (650, "Другой материал", "", "new"),
            ],
        )
        db.executemany(
            "INSERT INTO nlp_results VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    649,
                    "Чемпионат 2023",
                    "[]",
                    "publish",
                    json.dumps(
                        {
                            "cover": {"url": "cover.jpg"},
                            "owner_editing_text": "Чемпионат 2023",
                            "translated_text": "Чемпионат 2023",
                            "owner_display_title": "Чемпионат",
                        }
                    ),
                    "Чемпионат 2023 финал",
                    "Комментарий владельца",
                    "voice.ogg",
                    "before",
                    4,
                ),
                (
                    650,
                    "Саммари другой записи",
                    "[]",
                    "review",
                    "{}",
                    None,
                    None,
                    None,
                    "before",
                    1,
                ),
            ],
        )


def test_repair_only_redacts_selected_result_and_preserves_owner_media(tmp_path):
    path = tmp_path / "data.db"
    make_database(path)
    with sqlite3.connect(path) as db:
        items_before = db.execute("SELECT * FROM items ORDER BY id").fetchall()
        other_before = db.execute(
            "SELECT * FROM nlp_results WHERE item_id=650"
        ).fetchone()
    HOTFIX.repair_record(path)
    db = HOTFIX.open_db(path)
    try:
        result = dict(
            db.execute("SELECT * FROM nlp_results WHERE item_id=649").fetchone()
        )
        assert "нет текстового контента" in result["summary"]
        assert result["merged_text"] is None and result["decision"] == "review"
        assert result["author_notes"] == "Комментарий владельца"
        assert result["voice_file"] == "voice.ogg" and result["version"] == 5
        extra = json.loads(result["extra"])
        assert extra["cover"] == {"url": "cover.jpg"}
        assert extra["source_context_missing"] is True
        assert "owner_editing_text" not in extra and "translated_text" not in extra
        assert "owner_display_title" not in extra
        assert [
            tuple(r) for r in db.execute("SELECT * FROM items ORDER BY id")
        ] == items_before
        assert (
            tuple(db.execute("SELECT * FROM nlp_results WHERE item_id=650").fetchone())
            == other_before
        )
    finally:
        db.close()


def test_repair_rejects_new_source_text_without_database_changes(tmp_path):
    path = tmp_path / "data.db"
    make_database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE items SET transcript='Новый подтвержденный текст' WHERE id=649"
        )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="target_transcript_changed"):
        HOTFIX.repair_record(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_mid_install_failure_restores_code_and_restarts_only_target_service(
    tmp_path, monkeypatch
):
    from config.settings import config

    root = tmp_path / "server"
    root.mkdir()
    db_path = root / "data.db"
    make_database(db_path)
    before_db = db_path.read_bytes()
    before = {}
    patch = []
    for name in HOTFIX.FILES:
        label = 'LABEL = "<b>Саммари (NLP)</b>"\n' if name.endswith("views.py") else ""
        old, new = label + "value = 1\n", label + "value = 2\n"
        patch.append(
            "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile="a/" + name,
                    tofile="b/" + name,
                )
            )
        )
        raw = old.replace("Саммари (NLP)", "Кратко (факты)").encode()
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        before[name] = raw
    calls = []
    active = True

    def fake_service(*args):
        nonlocal active
        calls.append(args)
        if args == ("show", "--property=WorkingDirectory", "--value"):
            return str(root)
        if args[0] == "stop":
            active = False
        if args[0] == "start":
            active = True
        if args[0] == "is-active":
            assert active
        if args == ("show", "--property=MainPID", "--value"):
            return "777"
        return ""

    original_install = HOTFIX.install_file
    installed = 0

    def fail_second_install(path, raw, metadata=None):
        nonlocal installed
        installed += 1
        if installed == 2:
            raise RuntimeError("simulated_disk_failure")
        return original_install(path, raw, metadata)

    monkeypatch.chdir(root)
    monkeypatch.setattr(HOTFIX, "ROOT", root)
    monkeypatch.setattr(
        HOTFIX, "EXPECTED", {name: HOTFIX.blob(raw) for name, raw in before.items()}
    )
    monkeypatch.setattr(HOTFIX.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "TEXT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--apply"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        HOTFIX.subprocess,
        "check_output",
        lambda *args, **kwargs: "".join(patch).encode(),
    )
    monkeypatch.setattr(HOTFIX, "service", fake_service)
    monkeypatch.setattr(HOTFIX.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(HOTFIX, "install_file", fail_second_install)
    with pytest.raises(RuntimeError, match="simulated_disk_failure"):
        HOTFIX.main()
    assert active and calls.count(("start",)) == 1
    assert calls.count(("stop",)) == 2
    for name, raw in before.items():
        assert (root / name).read_bytes() == raw
    assert db_path.read_bytes() == before_db
    assert (
        len(list((root.parent / "backups").glob("source-context-649-*/data.sqlite")))
        == 1
    )
