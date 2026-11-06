BEGIN;

-- Commenters under channel posts (was used in code without a checked-in migration).
CREATE TABLE IF NOT EXISTS channel_commenters (
    commenter_id TEXT PRIMARY KEY,
    channel_url TEXT NOT NULL,
    channel_title TEXT,
    post_id TEXT,
    message_id TEXT,
    user_id TEXT,
    user_name TEXT,
    comment_text TEXT,
    comment_at TEXT,
    source_name TEXT,
    synced_to_sheet INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_channel_commenters_synced
    ON channel_commenters(synced_to_sheet);
CREATE INDEX IF NOT EXISTS idx_channel_commenters_channel
    ON channel_commenters(channel_url);

-- Unique message authors from regular groups / megagroups (not full member roster).
CREATE TABLE IF NOT EXISTS group_authors (
    author_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    group_title TEXT,
    user_id TEXT,
    user_name TEXT,
    last_message_id TEXT,
    last_message_at TEXT,
    message_snippet TEXT,
    messages_seen INTEGER NOT NULL DEFAULT 1,
    source_name TEXT,
    synced_to_sheet INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_group_authors_synced ON group_authors(synced_to_sheet);
CREATE INDEX IF NOT EXISTS idx_group_authors_group ON group_authors(group_id);

-- Per-group scan checkpoint (min_id / last scan).
CREATE TABLE IF NOT EXISTS group_authors_checkpoints (
    group_id TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

COMMIT;
