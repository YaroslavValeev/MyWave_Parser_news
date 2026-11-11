BEGIN;

-- Visible participants of megagroups / chats (not broadcast-channel subscribers).
CREATE TABLE IF NOT EXISTS group_members (
    member_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    group_title TEXT,
    user_id TEXT,
    user_name TEXT,
    is_bot INTEGER NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    source_name TEXT,
    synced_to_sheet INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_group_members_synced ON group_members(synced_to_sheet);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);

COMMIT;
