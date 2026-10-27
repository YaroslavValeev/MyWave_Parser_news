CREATE TABLE IF NOT EXISTS publication_claims (
    item_id INTEGER PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    channel_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('sending', 'unknown', 'delivered')),
    message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE RESTRICT
);
