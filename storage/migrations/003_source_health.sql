BEGIN;

-- Per-source telemetry for Content Engine Stage 1 (Reliability Agent).
CREATE TABLE IF NOT EXISTS source_health (
    source_key TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_error TEXT,
    last_latency_ms REAL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    collected_total INTEGER NOT NULL DEFAULT 0,
    parsed_total INTEGER NOT NULL DEFAULT 0,
    duplicates_total INTEGER NOT NULL DEFAULT 0,
    rejected_total INTEGER NOT NULL DEFAULT 0,
    errors_total INTEGER NOT NULL DEFAULT 0,
    last_collected INTEGER NOT NULL DEFAULT 0,
    last_parsed INTEGER NOT NULL DEFAULT 0,
    last_duplicates INTEGER NOT NULL DEFAULT 0,
    last_rejected INTEGER NOT NULL DEFAULT 0,
    last_errors INTEGER NOT NULL DEFAULT 0,
    last_ok INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_source_health_updated ON source_health(updated_at);
CREATE INDEX IF NOT EXISTS idx_source_health_fail_streak ON source_health(consecutive_failures);

COMMIT;
