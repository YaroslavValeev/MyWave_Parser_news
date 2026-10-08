import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest

SPEC = importlib.util.spec_from_file_location(
    "runtime_audit",
    Path(__file__).resolve().parents[1] / "scripts/audit_reconciled_runtime.py",
)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_runtime_audit_normalizes_crlf_and_reports_drift_without_writes(tmp_path):
    path = tmp_path / "active.py"
    path.write_bytes(b"value=1\r\n")
    raw = path.read_bytes()
    expected = {"active.py": AUDIT.digest(b"value=1\n"), "missing.py": "missing"}
    report = AUDIT.runtime_files(tmp_path, expected)
    assert report["matched_count"] == 1 and report["missing"] == ["missing.py"]
    assert path.read_bytes() == raw
    path.write_bytes(b"operator_change=2\n")
    assert AUDIT.runtime_files(tmp_path, expected)["different"] == {
        "active.py": AUDIT.digest(path.read_bytes())
    }
    with pytest.raises(Exception):
        AUDIT.database_state(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()


def test_database_audit_reads_only_and_does_not_expose_content(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY,status TEXT,source_context TEXT,content TEXT,transcript TEXT,link TEXT); CREATE TABLE nlp_results(item_id INTEGER,extra TEXT); CREATE TABLE publications(item_id INTEGER);"
        )
        db.executemany(
            "INSERT INTO items VALUES(?,?,NULL,'private owner text',NULL,NULL)",
            [(626, "review"), (649, "discarded")],
        )
    before = path.read_bytes()
    report = AUDIT.database_state(path)
    assert (
        report["626"]["status"] == "review" and report["649"]["status"] == "discarded"
    )
    assert report["626"]["source_bound"] is False and report["626"]["publications"] == 0
    assert "private owner text" not in repr(report) and path.read_bytes() == before
