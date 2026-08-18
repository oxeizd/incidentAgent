from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import memory_settings as legacy_memory_settings


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """
    Runtime configuration нового memory-модуля.

    Все Path должны быть абсолютными к моменту создания MemoryApplication.
    Это исключает создание разных SQLite БД при запуске API, CLI, тестов
    или worker-процесса из разных current working directory.
    """

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
    """
    Создаёт единую конфигурацию memory для API, CLI, graph runtime и тестов.

    `project_root` — корень репозитория, где лежат директории `app/` и `data/`.
    """
    root = project_root.resolve()

    database_path = _absolute_under_root(
        root,
        legacy_memory_settings.db_path,
    )

    return MemorySettings(
        database_path=database_path,
        schema_path=root / "app" / "memory" / "db" / "schema.sql",
        migrations_path=root / "app" / "memory" / "db" / "migrations",
        embedding_model_path=legacy_memory_settings.embeddings_model_path,
        vector_dimension=legacy_memory_settings.vector_dim,
        search_preview_limit=10,
        cleanup_interval_seconds=3600,
        import_index_batch_size=100,
        semantic_default_limit=100,
    )


def _absolute_under_root(root: Path, value: Path) -> Path:
    if value.is_absolute():
        return value

    return (root / value).resolve()