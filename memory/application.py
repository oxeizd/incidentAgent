from __future__ import annotations

import asyncio
from contextlib import suppress

from memory.artifacts.assignments.repository import AssignmentRepository
from memory.artifacts.assignments.search import AssignmentSearch
from memory.artifacts.incidents.repository import IncidentRepository
from memory.artifacts.incidents.search import IncidentSearch
from memory.artifacts.search_results.repository import SearchResultRepository
from memory.artifacts.threads.repository import ThreadRepository
from memory.db.connection import Database
from memory.embeddings.contracts import EmbeddingProvider
from memory.embeddings.service import EmbeddingService
from memory.imports.service import ImportService
from memory.jobs.cleanup import run_search_result_cleanup_forever
from memory.search.service import SearchOrchestrationService
from memory.settings import MemorySettings
from memory.vectors.backfill import VectorBackfillService
from memory.vectors.indexing import VectorIndexingService
from memory.vectors.repository import VectorRepository
from memory.vectors.semantic_orchestration import (
    SemanticSearchOrchestrationService,
)
from memory.vectors.semantic_search import SemanticSearchService
from memory.vectors.sqlite_vec import initialize_sqlite_vec


class MemoryApplication:
    """
    Composition root and lifecycle owner for the memory subsystem.

    All application entrypoints should create exactly one MemoryApplication
    instance and call start()/stop() during application lifespan.
    """

    def __init__(
        self,
        settings: MemorySettings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._settings = settings
        self._database = Database(settings.database_path)
        self._cleanup_task: asyncio.Task[None] | None = None

        self.incidents = IncidentRepository(self._database)
        self.assignments = AssignmentRepository(self._database)
        self.threads = ThreadRepository(self._database)
        self.search_results = SearchResultRepository(self._database)

        self.incident_search = IncidentSearch(self._database)
        self.assignment_search = AssignmentSearch(self._database)

        self.embeddings = embedding_provider or EmbeddingService(settings)

        self.vectors = VectorRepository(
            database=self._database,
            vector_dimension=settings.vector_dimension,
        )
        self.vector_indexing = VectorIndexingService(
            embeddings=self.embeddings,
            vectors=self.vectors,
        )
        self.vector_backfill = VectorBackfillService(
            database=self._database,
            vector_indexing=self.vector_indexing,
        )

        self.search = SearchOrchestrationService(
            search_result_repository=self.search_results,
            thread_repository=self.threads,
            incident_search=self.incident_search,
            assignment_search=self.assignment_search,
            default_preview_limit=settings.search_preview_limit,
            default_batch_size=500,
        )

        self.semantic_search = SemanticSearchService(
            embeddings=self.embeddings,
            vectors=self.vectors,
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
        )
        self.semantic = SemanticSearchOrchestrationService(
            semantic_search=self.semantic_search,
            search_result_repository=self.search_results,
            thread_repository=self.threads,
            default_preview_limit=settings.search_preview_limit,
            default_similarity_limit=settings.semantic_default_limit,
        )

        self.imports = ImportService(
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
            vector_indexing=self.vector_indexing,
            index_batch_size=settings.import_index_batch_size,
        )

    async def start(self) -> None:
        await self._database.initialize(
            schema_path=self._settings.schema_path,
            migrations_path=self._settings.migrations_path,
        )

        await initialize_sqlite_vec(
            database=self._database,
            vector_dimension=self._settings.vector_dimension,
        )

        self._cleanup_task = asyncio.create_task(
            run_search_result_cleanup_forever(
                self.search_results,
                interval_seconds=self._settings.cleanup_interval_seconds,
            ),
            name="memory-search-result-cleanup",
        )

    async def stop(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()

            with suppress(asyncio.CancelledError):
                await self._cleanup_task

            self._cleanup_task = None

        await self._database.close()

    async def healthcheck(self) -> dict[str, object]:
        """
        Minimal internal readiness check.

        Does not load the embedding model: model loading belongs to the first
        actual embedding request or an explicit warm-up operation.
        """
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM incidents) AS incidents_count,
                (SELECT COUNT(*) FROM assignments) AS assignments_count,
                (SELECT COUNT(*) FROM incident_vectors) AS incident_vectors_count,
                (SELECT COUNT(*) FROM assignment_vectors) AS assignment_vectors_count
            """
        )
        row = await cursor.fetchone()

        return {
            "status": "ok",
            "database_path": str(self._settings.database_path),
            "vector_dimension": self._settings.vector_dimension,
            "incidents_count": int(row["incidents_count"]),
            "assignments_count": int(row["assignments_count"]),
            "incident_vectors_count": int(row["incident_vectors_count"]),
            "assignment_vectors_count": int(row["assignment_vectors_count"]),
        }