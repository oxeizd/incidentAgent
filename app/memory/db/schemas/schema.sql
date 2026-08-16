CREATE TABLE IF NOT EXISTS incidents (
    number                  TEXT PRIMARY KEY,
    -- ВАЖНО: НЕ переводить на WITHOUT ROWID -- incident_vec индексируется
    -- по скрытому rowid (см. repository/vectors.py).
    created_at              TEXT,
    target_date             TEXT,
    plan_finish_date        TEXT,
    close_date              TEXT,
    detection_time          TEXT,
    work_group              TEXT,
    element_name            TEXT,
    system_name             TEXT,
    created_by              TEXT,
    executor_name           TEXT,
    status                  TEXT,
    description             TEXT,
    reason_inc              TEXT,
    solution                TEXT,
    impact                  TEXT,
    start_time              TEXT,
    end_time                TEXT,
    priority_code           TEXT,
    resolution_code         TEXT,
    registration_basis      TEXT,
    inc_type                TEXT,
    impact_custom_service   INTEGER,
    no_impact               INTEGER,
    stand                   TEXT,
    mttd                    REAL,
    mttr                    REAL,
    downtime                REAL,
    is_root                 INTEGER,
    month_created           INTEGER,
    quarter_created         INTEGER,
    ai_description          TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Намеренно без REFERENCES incidents(number) ON DELETE CASCADE.
    incident_id TEXT NOT NULL,
    task        TEXT,
    unit        TEXT,
    assignment  TEXT,
    deadline    TEXT,
    date        TEXT,
    ior         TEXT,
    responsible TEXT
);

CREATE INDEX IF NOT EXISTS idx_assignments_incident_id ON assignments(incident_id);

CREATE TABLE IF NOT EXISTS threads (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    artifact   TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);

-- ДОБАВЛЕНО: хранилище презентаций (см. app/memory/repository/presentations.py).
-- Намеренно НЕ LangGraph-артефакт внутри чекпоинта треда: "мои презентации"/
-- "общее хранилище" — это кросс-тредовые, кросс-пользовательские выборки ("все
-- презентации пользователя X" из разных чатов), которые из чекпоинтера
-- не достать без полного скана всех тредов. Обычная таблица с owner_user_id решает это
-- тем же способом, что threads/messages выше.
--
-- status='draft' — редактируется свободно через PATCH /api/v1/presentations/{id}.
-- publish_presentation() замораживает текущие fields в published_snapshot и
-- переключает status='published' — дальнейшие правки fields не меняют то,
-- что видно в общем хранилище (list_shared_presentations() читает только
-- published_snapshot), пока не будет явного повторного publish.
CREATE TABLE IF NOT EXISTS presentations (
    id                  TEXT PRIMARY KEY,
    owner_user_id       TEXT NOT NULL,
    thread_id           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft', -- 'draft' | 'published'
    fields              TEXT NOT NULL,                 -- JSON: collected dict из creator.py
    analysis_markdown   TEXT,
    published_snapshot  TEXT,                           -- JSON, см. комментарий выше
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    published_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_presentations_owner ON presentations(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_presentations_status ON presentations(status);

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (0);
