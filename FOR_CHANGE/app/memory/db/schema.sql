PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (0);


CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT,
    created_at  TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    artifact    TEXT,
    created_at  TEXT NOT NULL,

    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_created
ON messages(thread_id, created_at, id);


CREATE TABLE IF NOT EXISTS incidents (
    number                  TEXT PRIMARY KEY,

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
    priority_code           TEXT,
    resolution_code         TEXT,
    registration_basis      TEXT,
    inc_type                TEXT,
    stand                   TEXT,

    description             TEXT,
    resolution_description  TEXT,
    reason_inc              TEXT,
    solution                TEXT,
    impact                  TEXT,

    start_time              TEXT,
    end_time                TEXT,

    impact_custom_service   INTEGER NOT NULL DEFAULT 0
                            CHECK (impact_custom_service IN (0, 1)),
    no_impact               INTEGER NOT NULL DEFAULT 0
                            CHECK (no_impact IN (0, 1)),
    is_root                 INTEGER NOT NULL DEFAULT 0
                            CHECK (is_root IN (0, 1)),

    mttd                    REAL,
    mttr                    REAL,
    downtime                REAL,

    month_created           INTEGER,
    quarter_created         INTEGER,

    ai_description          TEXT,

    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_start_number
ON incidents(start_time DESC, number ASC);

CREATE INDEX IF NOT EXISTS idx_incidents_status_start
ON incidents(status, start_time DESC, number ASC);

CREATE INDEX IF NOT EXISTS idx_incidents_system_start
ON incidents(system_name, start_time DESC, number ASC);

CREATE INDEX IF NOT EXISTS idx_incidents_group_start
ON incidents(work_group, start_time DESC, number ASC);


CREATE TABLE IF NOT EXISTS assignments (
    id                  TEXT PRIMARY KEY,

    incident_id         TEXT,
    ior                 TEXT,

    task                TEXT,
    unit                TEXT,
    assignment          TEXT NOT NULL,
    responsible         TEXT,

    deadline            TEXT,
    assigned_at         TEXT,
    status              TEXT,

    source_payload      TEXT,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_deadline_id
ON assignments(deadline ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_assignments_incident_deadline
ON assignments(incident_id, deadline ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_assignments_ior_deadline
ON assignments(ior, deadline ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_assignments_responsible_deadline
ON assignments(responsible, deadline ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_assignments_status_deadline
ON assignments(status, deadline ASC, id ASC);


CREATE TABLE IF NOT EXISTS search_results (
    id                  TEXT PRIMARY KEY,
    owner_user_id       TEXT NOT NULL,
    source_thread_id    TEXT,

    entity              TEXT NOT NULL
                        CHECK (entity IN ('incidents', 'assignments')),

    query_json          TEXT NOT NULL,
    display_json        TEXT NOT NULL,

    total_count         INTEGER NOT NULL CHECK (total_count >= 0),
    status              TEXT NOT NULL DEFAULT 'building'
                        CHECK (status IN ('building', 'ready', 'failed')),
    artifact_version    INTEGER NOT NULL DEFAULT 1,

    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    invalidated_at      TEXT,

    FOREIGN KEY (source_thread_id) REFERENCES threads(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_search_results_owner_created
ON search_results(owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_search_results_expires
ON search_results(expires_at)
WHERE invalidated_at IS NULL;


CREATE TABLE IF NOT EXISTS search_result_items (
    search_result_id    TEXT NOT NULL,
    position            INTEGER NOT NULL CHECK (position >= 0),

    entity_id           TEXT NOT NULL,
    score               REAL,

    PRIMARY KEY (search_result_id, position),

    FOREIGN KEY (search_result_id)
        REFERENCES search_results(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_result_items_page
ON search_result_items(search_result_id, position);