BEGIN;

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT,
    content TEXT,
    link TEXT,
    date TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    checksum TEXT NOT NULL UNIQUE,
    lang TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    author_user_id INTEGER,
    images TEXT,
    videos TEXT,
    transcript TEXT,
    comment TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_checksum ON items(checksum);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at);

CREATE TABLE IF NOT EXISTS nlp_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL UNIQUE,
    summary TEXT,
    questions TEXT,
    decision TEXT,
    moderation TEXT,
    extra TEXT,
    author_notes TEXT,
    merged_text TEXT,
    voice_file TEXT,
    rewrite_guidance TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    username TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    meta TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS review_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    owner_user_id INTEGER,
    state TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

COMMIT;
