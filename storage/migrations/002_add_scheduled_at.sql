BEGIN;

ALTER TABLE items ADD COLUMN scheduled_at TEXT;
CREATE INDEX IF NOT EXISTS idx_items_scheduled_at ON items(scheduled_at);

COMMIT;
