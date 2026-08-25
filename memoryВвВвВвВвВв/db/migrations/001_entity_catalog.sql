CREATE TABLE IF NOT EXISTS entity_catalog (
    id                  TEXT PRIMARY KEY,

    entity_type         TEXT NOT NULL
                        CHECK (
                            entity_type IN (
                                'system_name',
                                'work_group',
                                'executor_name',
                                'element_name'
                            )
                        ),

    canonical_value     TEXT NOT NULL,
    normalized_value    TEXT NOT NULL,
    aliases_json        TEXT NOT NULL DEFAULT '[]',

    source_count        INTEGER NOT NULL DEFAULT 0
                        CHECK (source_count >= 0),

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    UNIQUE (entity_type, canonical_value)
);

CREATE INDEX IF NOT EXISTS idx_entity_catalog_type_normalized
ON entity_catalog(entity_type, normalized_value);

CREATE INDEX IF NOT EXISTS idx_entity_catalog_type_source_count
ON entity_catalog(
    entity_type,
    source_count DESC,
    canonical_value ASC
);