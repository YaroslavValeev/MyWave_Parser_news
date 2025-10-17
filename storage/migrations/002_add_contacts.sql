BEGIN;

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    date_found TEXT,
    item_link TEXT,
    item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);
CREATE INDEX IF NOT EXISTS idx_contacts_type ON contacts(type);

COMMIT;
