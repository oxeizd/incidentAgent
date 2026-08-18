ALTER TABLE threads
ADD COLUMN updated_at TEXT;

UPDATE threads
SET updated_at = created_at
WHERE updated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_threads_user_updated
ON threads(user_id, updated_at DESC, id DESC);