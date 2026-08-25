from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Literal

from app.memory.artifacts.assignments.repository import AssignmentRepository
from app.memory.artifacts.assignments.search import AssignmentSearch
from app.memory.artifacts.incident_reports.repository import (
    IncidentReportRepository,
)
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.artifacts.incidents.search import IncidentSearch
from app.memory.artifacts.presentations.repository import (
    PresentationRepository,
)
from app.memory.artifacts.search_results.repository import (
    SearchResultRepository,
)
from app.memory.artifacts.threads.repository import ThreadRepository
from app.memory.catalog.repository import EntityCatalogRepository
from app.memory.catalog.resolver import EntityCatalogResolver
from app.memory.catalog.service import EntityCatalogService
from app.memory.db.connection import Database
from app.memory.embeddings.contracts import EmbeddingProvider
from app.memory.embeddings.service import EmbeddingService
from app.memory.imports.service import ImportService
from app.memory.jobs.cleanup import run_search_result_cleanup_forever
from app.memory.search.result_writer import SearchResultWriter
from app.memory.search.service import SearchOrchestrationService
from app.memory.settings import MemorySettings
from app.memory.vectors.backfill import VectorBackfillService
from app.memory.vectors.indexing import VectorIndexingService
from app.memory.vectors.repository import VectorRepository
from app.memory.vectors.semantic_orchestration import (
    SemanticSearchOrchestrationService,
)
from app.memory.vectors.semantic_search import SemanticSearchService
from app.memory.vectors.sqlite_vec import initialize_sqlite_vec


logger = logging.getLogger(__name__)

VectorEntity = Literal["incidents", "assignments", "all"]

_CATALOG_FIELDS = (
    ("system_name", "system_name"),
    ("work_group", "work_group"),
    ("executor_name", "executor_name"),
    ("element_name", "element_name"),
)


class MemoryApplication:
    """Composition root memory-модуля."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)

        self._started = False
        self.cleanup_task: asyncio.Task[None] | None = None

        self.incidents = IncidentRepository(self.database)
        self.assignments = AssignmentRepository(self.database)
        self.threads = ThreadRepository(self.database)
        self.presentations = PresentationRepository(self.database)
        self.incident_reports = IncidentReportRepository(self.database)
        self.search_results = SearchResultRepository(self.database)

        self.entity_catalog_repository = EntityCatalogRepository(
            self.database
        )
        self.entity_catalog = EntityCatalogService(
            repository=self.entity_catalog_repository
        )
        self.entity_resolver = EntityCatalogResolver(
            catalog=self.entity_catalog
        )

        self.incident_search = IncidentSearch(self.database)
        self.assignment_search = AssignmentSearch(self.database)

        self.embeddings = embedding_provider or EmbeddingService(settings)

        self.vectors = VectorRepository(
            database=self.database,
            vector_dimension=settings.vector_dimension,
        )

        self.vector_indexing = VectorIndexingService(
            embeddings=self.embeddings,
            vectors=self.vectors,
        )

        self.vector_backfill = VectorBackfillService(
            database=self.database,
            vector_indexing=self.vector_indexing,
        )

        self.semantic_search = SemanticSearchService(
            embeddings=self.embeddings,
            vectors=self.vectors,
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
        )

        self.search_result_writer = SearchResultWriter(
            search_result_repository=self.search_results,
            thread_repository=self.threads,
            default_preview_limit=settings.search_preview_limit,
        )

        self.search = SearchOrchestrationService(
            result_writer=self.search_result_writer,
            incident_search=self.incident_search,
            assignment_search=self.assignment_search,
            default_batch_size=500,
        )

        self.semantic = SemanticSearchOrchestrationService(
            semantic_search=self.semantic_search,
            incident_search=self.incident_search,
            assignment_search=self.assignment_search,
            result_writer=self.search_result_writer,
            default_similarity_limit=settings.semantic_default_limit,
        )

        self.imports = ImportService(
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
            vector_indexing=self.vector_indexing,
            entity_catalog=self.entity_catalog,
            index_batch_size=settings.import_index_batch_size,
        )

    async def start(self) -> None:
        if self._started:
            return

        try:
            await self.database.initialize(
                schema_path=self.settings.schema_path,
                migrations_path=self.settings.migrations_path,
            )

            await initialize_sqlite_vec(
                database=self.database,
                vector_dimension=self.settings.vector_dimension,
            )

            self.cleanup_task = asyncio.create_task(
                run_search_result_cleanup_forever(
                    self.search_results,
                    interval_seconds=self.settings.cleanup_interval_seconds,
                ),
                name="memory-search-result-cleanup",
            )

            self._started = True
        except Exception:
            logger.exception("MemoryApplication startup failed")
            await self.stop()
            raise

    async def stop(self) -> None:
        cleanup_task = self.cleanup_task
        self.cleanup_task = None

        if cleanup_task is not None:
            cleanup_task.cancel()

            with suppress(asyncio.CancelledError):
                await cleanup_task

        await self.database.close()
        self._started = False

    async def healthcheck(self) -> dict[str, object]:
        connection = await self.database.read_connection()

        cursor = await connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM incidents) AS incidents_count,
                (SELECT COUNT(*) FROM assignments) AS assignments_count,
                (SELECT COUNT(*) FROM incident_vectors)
                    AS incident_vectors_count,
                (SELECT COUNT(*) FROM assignment_vectors)
                    AS assignment_vectors_count,
                (SELECT COUNT(*) FROM threads) AS threads_count,
                (SELECT COUNT(*) FROM presentations)
                    AS presentations_count,
                (SELECT COUNT(*) FROM incident_reports)
                    AS incident_reports_count,
                (SELECT COUNT(*) FROM entity_catalog)
                    AS entity_catalog_count
            """
        )
        row = await cursor.fetchone()

        return {
            "status": "ok",
            "database_path": str(self.settings.database_path),
            "vector_dimension": self.settings.vector_dimension,
            "incidents_count": int(row["incidents_count"]),
            "assignments_count": int(row["assignments_count"]),
            "incident_vectors_count": int(
                row["incident_vectors_count"]
            ),
            "assignment_vectors_count": int(
                row["assignment_vectors_count"]
            ),
            "threads_count": int(row["threads_count"]),
            "presentations_count": int(
                row["presentations_count"]
            ),
            "incident_reports_count": int(
                row["incident_reports_count"]
            ),
            "entity_catalog_count": int(
                row["entity_catalog_count"]
            ),
        }

    async def rebuild_vector_indexes(
        self,
        *,
        entity: VectorEntity = "all",
        batch_size: int = 100,
    ) -> dict[str, int]:
        """
        Rebuilds vectors for selected entity or both entity types.

        Используется CLI/admin layer после изменения embedding model,
        vector dimension или embedding document policy.
        """
        if not self._started:
            raise RuntimeError(
                "MemoryApplication must be started before vector rebuild"
            )

        if entity not in ("incidents", "assignments", "all"):
            raise ValueError(f"Unsupported vector entity: {entity!r}")

        result: dict[str, int] = {}

        if entity in ("incidents", "all"):
            result["incidents"] = (
                await self.vector_backfill.backfill_incidents(
                    batch_size=batch_size,
                )
            )

        if entity in ("assignments", "all"):
            result["assignments"] = (
                await self.vector_backfill.backfill_assignments(
                    batch_size=batch_size,
                )
            )

        logger.info(
            "Vector rebuild completed: entity=%s result=%s",
            entity,
            result,
        )

        return result

    async def rebuild_entity_catalog(self) -> dict[str, int]:
        if not self._started:
            raise RuntimeError(
                "MemoryApplication must be started before catalog rebuild"
            )

        connection = await self.database.read_connection()
        result: dict[str, int] = {}

        for entity_type, column_name in _CATALOG_FIELDS:
            cursor = await connection.execute(
                f"""
                SELECT {column_name} AS value
                FROM incidents
                WHERE {column_name} IS NOT NULL
                  AND TRIM({column_name}) <> ''
                """
            )
            rows = await cursor.fetchall()

            result[entity_type] = (
                await self.entity_catalog.refresh_values(
                    entity_type=entity_type,
                    values=(row["value"] for row in rows),
                )
            )

        await self.entity_resolver.refresh()

        logger.info(
            "Entity catalog rebuild completed: %s",
            result,
        )
        return result