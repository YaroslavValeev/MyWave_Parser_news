"""Observe queue/receipt/claim state read-only. Does not apply a migration or authorize rollout."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def inspect(database: Path):
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("database_check_failed")
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        claims = (
            dict(
                db.execute(
                    "SELECT state,count(*) FROM publication_claims GROUP BY state"
                )
            )
            if "publication_claims" in tables
            else None
        )
        queued = dict(
            db.execute(
                "SELECT status,count(*) FROM items WHERE status IN ('approved','ready_to_publish','publish_retry') GROUP BY status"
            )
        )
        legacy = db.execute(
            "SELECT count(*) FROM items WHERE status IN ('approved','ready_to_publish','publish_retry') AND EXISTS (SELECT 1 FROM publications WHERE item_id=items.id)"
        ).fetchone()[0]
        receipt_count, last_receipt = db.execute(
            "SELECT count(*),max(published_at) FROM publications"
        ).fetchone()
        migration = (
            bool(
                db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version='005_publication_claims'"
                ).fetchone()
            )
            if "schema_migrations" in tables
            else False
        )
    return {
        "readiness": "observation_only",
        "database_access": "read_only",
        "sqlite_version": sqlite3.sqlite_version,
        "queued_status_counts": queued,
        "queued_items_with_existing_receipt": legacy,
        "stored_receipts": receipt_count,
        "last_recorded_at": last_receipt,
        "claims_table_present": claims is not None,
        "claim_state_counts": claims,
        "migration_recorded": migration,
        "deploy_ready": False,
        "remaining_gate": "review_all_writers_and_unresolved_deliveries_before_rollout",
        "database_writes": 0,
        "external_requests": 0,
        "telegram_sends": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.database), ensure_ascii=False))
    except (OSError, ValueError, sqlite3.Error):
        print(
            json.dumps(
                {"readiness": "failed", "reason": "invalid_or_unavailable_database"}
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
