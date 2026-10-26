"""Evidence joins are read-only, idempotent and never turn a private probe into a KPI."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_publication_funnel.py"
SPEC = importlib.util.spec_from_file_location("publication_funnel", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            (SCRIPT.parents[1] / "storage/migrations/001_init.sql").read_text(
                encoding="utf-8"
            )
        )
        db.executescript(
            """
            INSERT INTO items(id,source,status,checksum) VALUES
                (626,'fixture','review','fixture-626'),(649,'fixture','discarded','fixture-649'),
                (7,'fixture','published','fixture-7');
            INSERT INTO publications VALUES(1,7,'-1009001','25','2026-10-09T10:00:00Z');
        """
        )
    return path


def events_file(tmp_path, *events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    return path


def event(
    kind,
    event_id,
    *,
    pub="parser-news:1",
    transition="transition-1",
    request="request-1",
    when="2026-10-25T10:00:00Z"
):
    row = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": kind,
        "event_source": "intake_server" if kind == "request_created" else "site_server",
        "publication_id": pub,
        "occurred_at": when,
    }
    if kind != "view":
        row["transition_id"] = transition
    if kind == "request_created":
        row["request_id"] = request
    return row


def test_missing_measurement_stays_unknown_and_missing_database_is_not_created(
    database, tmp_path
):
    before = database.read_bytes()
    report = AUDIT.audit(database)
    assert report["database_access"] == "read_only"
    assert report["receipt_counts"]["unique_eligible_receipts"] == 1
    row = report["deliveries"][0]
    assert row["publication_id"] == "parser-news:1" and row["source_item_id"] == 7
    assert row["provided_event_counts"] is None and row["publication_url"] is None
    assert row["source_revision_at_delivery"] is None
    assert report["event_input"] is None
    assert report["business_kpi_verification"] == "not_independently_verified"
    assert database.read_bytes() == before
    absent = tmp_path / "absent.db"
    with pytest.raises(sqlite3.Error):
        AUDIT.audit(absent)
    assert not absent.exists()


def test_publication_receipt_transition_request_join_is_idempotent_and_redacts_contacts(
    database, tmp_path
):
    before = database.read_bytes()
    click = event("click", "click-event")
    request = event("request_created", "request-event", when="2026-10-25T10:01:00Z")
    request.update(
        email="private@example.test", phone="private-phone", text="private-form"
    )
    path = events_file(
        tmp_path, event("view", "view-event"), click, click, request, request
    )
    report = AUDIT.audit(database, events=path)
    assert report == AUDIT.audit(database, events=path)
    assert report["deliveries"][0]["provided_event_counts"] == {
        "view_events": 1,
        "transitions": 1,
        "request_records": 1,
        "attributable_request_candidates": 1,
    }
    assert report["event_input"]["duplicate_event_rows"] == 2
    assert report["event_input"]["verification"] == "provided_input_only"
    assert (
        report["production_database_writes"]
        == report["external_requests"]
        == report["sends"]
        == 0
    )
    encoded = json.dumps(report)
    assert "private@example.test" not in encoded and "private-phone" not in encoded
    assert "private-form" not in encoded and "-1009001" not in encoded
    assert database.read_bytes() == before


def test_private_probe_is_excluded_even_if_copied_into_publications(database, tmp_path):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO publications VALUES(2,626,'9001','6817','2026-10-09T10:00:00Z')"
        )
    receipt = tmp_path / "private-probe.json"
    receipt.write_text(
        json.dumps(
            {
                "state": "delivered",
                "chat_id": 9001,
                "kind": "text",
                "message_id": 6817,
                "manual_text_verified": True,
            }
        ),
        encoding="utf-8",
    )
    before = database.read_bytes()
    report = AUDIT.audit(database, probe_receipts=[receipt])
    assert report["receipt_counts"]["stored_rows"] == 2
    assert report["receipt_counts"]["unique_eligible_receipts"] == 1
    assert report["receipt_counts"]["rejected"] == [
        {"publication_id": "parser-news:2", "reason": "private_probe"}
    ]
    assert report["private_probes"][0]["business_eligible"] is False
    assert report["private_probes"][0]["manual_text_verified"] is True
    assert database.read_bytes() == before


def test_duplicate_stored_ack_has_one_canonical_publication_and_alias(
    database, tmp_path
):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO publications VALUES(2,7,'-1009001','25','2026-10-09T10:00:00Z')"
        )
    path = events_file(
        tmp_path,
        event("click", "c", pub="parser-news:2"),
        event("request_created", "r", when="2026-10-25T10:01:00Z"),
    )
    report = AUDIT.audit(database, events=path)
    assert report["receipt_counts"]["unique_eligible_receipts"] == 1
    assert report["receipt_counts"]["duplicate_bookkeeping_rows"] == 1
    assert report["deliveries"][0]["aliases"] == ["parser-news:2"]
    assert (
        report["deliveries"][0]["provided_event_counts"][
            "attributable_request_candidates"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        "wrong-publication",
        "wrong-transition",
        "before-click",
        "client-event",
        "missing-request-id",
    ],
)
def test_unproven_request_is_not_attributed(database, tmp_path, change):
    click = event("click", "c")
    request = event("request_created", "r", when="2026-10-25T10:01:00Z")
    if change == "wrong-publication":
        request["publication_id"] = "parser-news:626"
    elif change == "wrong-transition":
        request["transition_id"] = "another-transition"
    elif change == "before-click":
        request["occurred_at"] = "2026-10-25T09:59:59Z"
    elif change == "client-event":
        request["event_source"] = "browser"
    else:
        request.pop("request_id")
    report = AUDIT.audit(database, events=events_file(tmp_path, click, request))
    assert (
        report["deliveries"][0]["provided_event_counts"][
            "attributable_request_candidates"
        ]
        == 0
    )


def test_conflicting_event_and_request_ids_are_quarantined(database, tmp_path):
    click = event("click", "c")
    conflict = {**click, "transition_id": "conflicting-transition"}
    first = event("request_created", "r1", when="2026-10-25T10:01:00Z")
    second = event(
        "request_created", "r2", transition="different", when="2026-10-25T10:01:00Z"
    )
    report = AUDIT.audit(
        database, events=events_file(tmp_path, click, conflict, first, second)
    )
    assert report["event_input"]["conflicting_event_ids"] == 1
    assert report["join_diagnostics"]["ambiguous_requests"] == 1
    assert (
        report["deliveries"][0]["provided_event_counts"][
            "attributable_request_candidates"
        ]
        == 0
    )


def test_conflicting_receipt_sources_are_not_counted(database):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO publications VALUES(2,626,'-1009001','25','2026-10-09T10:00:00Z')"
        )
    report = AUDIT.audit(database)
    assert report["deliveries"] == []
    assert {r["reason"] for r in report["receipt_counts"]["rejected"]} == {
        "conflicting_source_items"
    }


def test_cli_is_read_only_and_requires_no_network_or_application_secrets(database):
    before = database.read_bytes()
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--database", str(database)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert json.loads(result.stdout)["sends"] == 0
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "change", ["naive-time", "numeric-time", "boolean-version", "json-list"]
)
def test_invalid_event_shapes_do_not_crash_or_enter_counts(database, tmp_path, change):
    value = event("click", "c")
    if change == "naive-time":
        value["occurred_at"] = "2026-10-25T10:00:00"
    elif change == "numeric-time":
        value["occurred_at"] = 123
    elif change == "boolean-version":
        value["schema_version"] = True
    else:
        value = []
    report = AUDIT.audit(database, events=events_file(tmp_path, value))
    assert report["event_input"]["rejected"] == [
        {"line": 1, "reason": "invalid_event_contract"}
    ]
    assert report["deliveries"][0]["provided_event_counts"]["transitions"] == 0


def test_equal_instants_deduplicate_and_report_event_time_bounds(database, tmp_path):
    first = event("click", "c", when="2026-10-25T10:00:00Z")
    second = {**first, "occurred_at": "2026-10-25T13:00:00+03:00"}
    report = AUDIT.audit(database, events=events_file(tmp_path, first, second))
    assert report["event_input"]["duplicate_event_rows"] == 1
    assert (
        report["event_input"]["event_time_min"]
        == report["event_input"]["event_time_max"]
        == "2026-10-25T10:00:00+00:00"
    )


def test_transition_reused_across_publications_is_not_credited(database, tmp_path):
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO publications VALUES(2,626,'-1009001','26','2026-10-09T10:00:00Z')"
        )
    path = events_file(
        tmp_path,
        event("click", "c1"),
        event("click", "c2", pub="parser-news:2"),
        event("request_created", "r", when="2026-10-25T10:01:00Z"),
    )
    report = AUDIT.audit(database, events=path)
    assert report["join_diagnostics"]["ambiguous_transitions"] == 1
    assert (
        sum(
            row["provided_event_counts"]["attributable_request_candidates"]
            for row in report["deliveries"]
        )
        == 0
    )


def test_empty_export_is_measured_only_within_provided_input(database, tmp_path):
    report = AUDIT.audit(database, events=events_file(tmp_path))
    assert report["deliveries"][0]["provided_event_counts"]["view_events"] == 0
    assert report["event_input"]["verification"] == "provided_input_only"
    assert report["event_input"]["event_time_min"] is None
    assert report["business_kpi_verification"] == "not_independently_verified"


def test_release_snapshot_keeps_verified_overlay_hashes_and_unknown_kpis():
    import hashlib

    root = SCRIPT.parents[1]
    record = json.loads(
        (root / "docs/releases/parser-editorial-20261025.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["release_path"]["selected"] == [7, 8]
    assert record["release_path"]["new_deploy_authorized"] is False
    assert record["release_path"]["new_sends_authorized"] is False
    assert record["historical_private_probe"]["business_kpi"] is False
    assert record["historical_private_probe"]["production_publication_id"] is None
    assert record["business_metrics_current"]["attributed_requests"] is None
    for artifact in record["runtime"]["overlay_chain"]:
        manifest = root / artifact["manifest"]
        assert (
            hashlib.sha256(manifest.read_bytes()).hexdigest()
            == artifact["manifest_sha256"]
        )
        assert (
            hashlib.sha256((manifest.parent / "overlay.patch").read_bytes()).hexdigest()
            == artifact["patch_sha256"]
        )
