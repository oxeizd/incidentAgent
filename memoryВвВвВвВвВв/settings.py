from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MemorySettings:
    database_path: Path
    schema_path: Path
    migrations_path: Path

    embedding_model_path: str
    vector_dimension: int

    search_preview_limit: int = 10
    cleanup_interval_seconds: int = 3600
    import_index_batch_size: int = 100
    semantic_default_limit: int = 100


def default_memory_settings(project_root: Path) -> MemorySettings:
    root = project_root.resolve()

    return MemorySettings(
        database_path=root / "data" / "runtime" / "memory.sqlite3",
        schema_path=root / "app" / "memory" / "db" / "schema.sql",
        migrations_path=(
            root / "app" / "memory" / "db" / "migrations"
        ),
        embedding_model_path=str(root / "data" / "models" / "rubert-mini"),
        vector_dimension=312,
        search_preview_limit=10,
        cleanup_interval_seconds=3600,
        import_index_batch_size=100,
        semantic_default_limit=100,
    )