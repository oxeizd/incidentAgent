from __future__ import annotations

import asyncio
from contextlib import suppress

from app.memory.agent_retrieval import AgentRetrievalService
from app.memory.artifacts.assignments.repository import AssignmentRepository
from app.memory.artifacts.assignments.search import AssignmentSearch
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.artifacts.incidents.search import IncidentSearch
from app.memory.artifacts.presentations.repository import PresentationRepository
from app.memory.artifacts.search_results.repository import SearchResultRepository
from app.memory.artifacts.threads.repository import ThreadRepository
from app.memory.db.connection import Database
from app.memory.embeddings.contracts import EmbeddingProvider
from app.memory.embeddings.service import EmbeddingService
from app.memory.imports.service import ImportService
from app.memory.jobs.cleanup import run_search_result_cleanup_forever
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


class MemoryApplication:
    """
    Composition root memory-модуля.

    `search`/`semantic` — UI flows с persisted artifacts.
    `agent_retrieval` — чистые bounded read-only данные для AI graph.
    """

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
        self.search_results = SearchResultRepository(self.database)

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

        self.agent_retrieval = AgentRetrievalService(
            incident_search=self.incident_search,
            assignment_search=self.assignment_search,
            semantic_search=self.semantic_search,
        )

        self.imports = ImportService(
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
            vector_indexing=self.vector_indexing,
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
                    AS presentations_count
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
        }