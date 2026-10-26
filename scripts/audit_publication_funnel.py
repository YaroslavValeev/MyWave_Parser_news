"""Read-only publication/receipt/event reconciliation. Never sends or changes a database."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_key(producer, chat_id, message_id):
    return fingerprint([producer, str(chat_id), str(message_id)])


def read_probe_receipts(paths, producer):
    probes, keys = [], set()
    for path in paths:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("state") not in {
            "send_started",
            "delivered",
        }:
            raise ValueError("invalid_probe_receipt")
        if type(value.get("chat_id")) is not int or value["chat_id"] <= 0:
            raise ValueError("probe_must_be_private")
        message_id = value.get("message_id")
        if value["state"] == "delivered":
            if type(message_id) is not int or message_id <= 0:
                raise ValueError("invalid_probe_message_id")
            keys.add(receipt_key(producer, value["chat_id"], message_id))
        probes.append(
            {
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "state": value["state"],
                "message_id": message_id,
                "manual_text_verified": value.get("manual_text_verified") is True,
                "classification": "private_smoke",
                "business_eligible": False,
            }
        )
    return probes, keys


def read_deliveries(database, producer, excluded_keys):
    # mode=ro refuses a missing path; query_only and BEGIN retain a read snapshot.
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("database_check_failed")
        rows = db.execute(
            """
            SELECT p.id,p.item_id,p.channel_id,p.message_id,p.published_at,i.id AS source_id
            FROM publications p LEFT JOIN items i ON i.id=p.item_id ORDER BY p.id
        """
        ).fetchall()
    grouped, rejected = defaultdict(list), []
    for row in rows:
        value = dict(row)
        if (
            value["source_id"] is None
            or not str(value["channel_id"] or "").strip()
            or not str(value["message_id"]).isdigit()
            or int(value["message_id"]) <= 0
        ):
            rejected.append(
                {
                    "publication_id": f"{producer}:{value['id']}",
                    "reason": "invalid_stored_receipt",
                }
            )
            continue
        grouped[receipt_key(producer, value["channel_id"], value["message_id"])].append(
            value
        )
    deliveries, aliases = [], {}
    duplicate_rows = 0
    for key, group in grouped.items():
        ids = [f"{producer}:{row['id']}" for row in group]
        if key in excluded_keys or len({row["item_id"] for row in group}) != 1:
            reason = (
                "private_probe" if key in excluded_keys else "conflicting_source_items"
            )
            rejected.extend(
                {"publication_id": pub_id, "reason": reason} for pub_id in ids
            )
            continue
        canonical = ids[0]
        aliases.update({pub_id: canonical for pub_id in ids})
        duplicate_rows += len(group) - 1
        deliveries.append(
            {
                "publication_id": canonical,
                "aliases": ids[1:],
                "source_item_id": group[0]["item_id"],
                "receipt_key": key,
                "telegram_message_id": str(group[0]["message_id"]),
                "recorded_at": group[0]["published_at"],
                "evidence_level": "stored_ack_record",
                "publication_url": None,
                "source_revision_at_delivery": None,
                "final_text_sha256_at_delivery": None,
            }
        )
    return (
        deliveries,
        aliases,
        {
            "stored_rows": len(rows),
            "unique_eligible_receipts": len(deliveries),
            "duplicate_bookkeeping_rows": duplicate_rows,
            "rejected": rejected,
        },
    )


def parse_events(path):
    raw = Path(path).read_bytes()
    unique, conflicts, rejected, duplicates = {}, set(), [], 0
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if (
                not isinstance(value, dict)
                or type(value.get("schema_version")) is not int
                or value["schema_version"] != 1
            ):
                raise ValueError()
            kind = value.get("event_type")
            expected_origin = {
                "view": "site_server",
                "click": "site_server",
                "request_created": "intake_server",
            }
            if (
                kind not in expected_origin
                or value.get("event_source") != expected_origin[kind]
            ):
                raise ValueError()
            keys = ["event_id", "publication_id"]
            if kind != "view":
                keys.append("transition_id")
            if kind == "request_created":
                keys.append("request_id")
            if any(
                not isinstance(value.get(k), str) or not ID.fullmatch(value[k])
                for k in keys
            ):
                raise ValueError()
            if not isinstance(value.get("occurred_at"), str):
                raise ValueError()
            when = datetime.fromisoformat(value["occurred_at"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError()
            # Export only contract fields; contact/form values never enter a report.
            event = {
                k: value[k]
                for k in [
                    "schema_version",
                    "event_type",
                    "event_source",
                    "occurred_at",
                    *keys,
                ]
            }
            event["occurred_at"] = when.astimezone(timezone.utc).isoformat()
            event["when"] = when
        except (KeyError, TypeError, ValueError):
            rejected.append({"line": line_no, "reason": "invalid_event_contract"})
            continue
        event_id = event["event_id"]
        if event_id in unique:
            if event == unique[event_id]:
                duplicates += 1
            else:
                conflicts.add(event_id)
        else:
            unique[event_id] = event
    for event_id in conflicts:
        unique.pop(event_id, None)
    times = [event["when"].astimezone(timezone.utc) for event in unique.values()]
    return list(unique.values()), {
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "verification": "provided_input_only",
        "duplicate_event_rows": duplicates,
        "conflicting_event_ids": len(conflicts),
        "rejected": rejected,
        "event_time_min": min(times).isoformat() if times else None,
        "event_time_max": max(times).isoformat() if times else None,
    }


def reconcile_events(events, aliases):
    counts = {
        pub: {
            "view_events": 0,
            "transitions": 0,
            "request_records": 0,
            "attributable_request_candidates": 0,
        }
        for pub in set(aliases.values())
    }
    clicks, requests = defaultdict(list), defaultdict(list)
    unresolved = 0
    for event in events:
        pub = aliases.get(event["publication_id"])
        if pub is None:
            unresolved += 1
            continue
        event = {**event, "publication_id": pub}
        if event["event_type"] == "view":
            counts[pub]["view_events"] += 1
        elif event["event_type"] == "click":
            clicks[event["transition_id"]].append(event)
        else:
            requests[event["request_id"]].append(event)
    valid_clicks = {}
    ambiguous_clicks = 0
    for transition, group in clicks.items():
        if len({(e["publication_id"], e["when"]) for e in group}) != 1:
            ambiguous_clicks += 1
            continue
        valid_clicks[transition] = group[0]
        counts[group[0]["publication_id"]]["transitions"] += 1
    ambiguous_requests, unattributed_requests = 0, 0
    for group in requests.values():
        if (
            len({(e["publication_id"], e["transition_id"], e["when"]) for e in group})
            != 1
        ):
            ambiguous_requests += 1
            continue
        request = group[0]
        pub = request["publication_id"]
        counts[pub]["request_records"] += 1
        click = valid_clicks.get(request["transition_id"])
        if (
            click
            and click["publication_id"] == pub
            and click["when"] <= request["when"]
        ):
            counts[pub]["attributable_request_candidates"] += 1
        else:
            unattributed_requests += 1
    return counts, {
        "unresolved_publication_events": unresolved,
        "ambiguous_transitions": ambiguous_clicks,
        "ambiguous_requests": ambiguous_requests,
        "requests_without_matching_transition": unattributed_requests,
    }


def audit(database, *, producer="parser-news", events=None, probe_receipts=()):
    if not isinstance(producer, str) or not ID.fullmatch(producer) or ":" in producer:
        raise ValueError("invalid_producer_namespace")
    probes, exclusions = read_probe_receipts(probe_receipts, producer)
    deliveries, aliases, receipt_counts = read_deliveries(
        database, producer, exclusions
    )
    event_input, joins = None, None
    if events is not None:
        records, event_input = parse_events(events)
        counts, joins = reconcile_events(records, aliases)
        for row in deliveries:
            row["provided_event_counts"] = counts[row["publication_id"]]
    else:
        for row in deliveries:
            row["provided_event_counts"] = None
    return {
        "schema_version": 1,
        "producer": producer,
        "database_access": "read_only",
        "business_kpi_verification": "not_independently_verified",
        "deliveries": deliveries,
        "receipt_counts": receipt_counts,
        "event_input": event_input,
        "join_diagnostics": joins,
        "private_probes": probes,
        "production_database_writes": 0,
        "external_requests": 0,
        "sends": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--producer", default="parser-news")
    parser.add_argument("--events", type=Path)
    parser.add_argument("--probe-receipt", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        result = audit(
            args.database,
            producer=args.producer,
            events=args.events,
            probe_receipts=args.probe_receipt,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError):
        print(
            json.dumps({"audit": "failed", "reason": "invalid_or_unavailable_evidence"})
        )
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
