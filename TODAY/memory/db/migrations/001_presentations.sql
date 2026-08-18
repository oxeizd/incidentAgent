CREATE TABLE IF NOT EXISTS presentations (
    id                  TEXT PRIMARY KEY,
    owner_user_id       TEXT NOT NULL,
    thread_id           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'published')),
    fields              TEXT NOT NULL,
    published_snapshot  TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    published_at        TEXT,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_presentations_owner_updated
ON presentations(owner_user_id, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_presentations_status_published
ON presentations(status, published_at DESC, id DESC);