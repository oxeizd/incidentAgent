from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Literal

from app.memory.analytics.service import AnalyticsSqlService
from app.memory.artifacts.assignments.repository import (
    AssignmentRepository,
)
from app.memory.artifacts.incident_reports.repository import (
    IncidentReportRepository,
)
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.artifacts.presentations.repository import (
    PresentationRepository,
)
from app.memory.artifacts.search_results.repository import (
    SearchResultRepository,
)
from app.memory.artifacts.threads.repository import ThreadRepository
from app.memory.artifacts.catalog.repository import EntityCatalogRepository
from app.memory.artifacts.catalog.resolver import EntityCatalogResolver
from app.memory.artifacts.catalog.service import EntityCatalogService
from app.memory.db.connection import Database
from app.memory.vectors.embeddings.contracts import EmbeddingProvider
from app.memory.vectors.embeddings.service import EmbeddingService
from app.memory.artifacts.imports.contracts import (
    ImportEntity,
    ImportErrorItem,
    ImportReport,
)
from app.memory.artifacts.imports.service import ImportService
from app.memory.jobs.cleanup import run_search_result_cleanup_forever
from app.memory.search.result_writer import SearchResultWriter
from app.memory.search.service import SearchOrchestrationService
from app.memory.search.structured import (
    ASSIGNMENT_SEARCH_CONFIG,
    INCIDENT_SEARCH_CONFIG,
    StructuredSearchService,
)
from app.memory.settings import MemorySettings
from app.memory.vectors.backfill import VectorBackfillService
from app.memory.vectors.indexing import VectorIndexingService
from app.memory.vectors.repository import VectorRepository
from app.memory.vectors.semantic_search import SemanticSearchService
from app.memory.vectors.sqlite_vec import initialize_sqlite_vec


logger = logging.getLogger(__name__)

VectorEntity = Literal["incidents", "assignments", "all"]

_CATALOG_FIELDS: tuple[tuple[str, str], ...] = (
    ("system_name", "system_name"),
    ("work_group", "work_group"),
    ("executor_name", "executor_name"),
    ("element_name", "element_name"),
)


class MemoryApplication:
    """Composition root for the memory subsystem."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)

        self.started = False
        self._cleanup_task: asyncio.Task[None] | None = None

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

        self.incident_search = StructuredSearchService(
            database=self.database,
            config=INCIDENT_SEARCH_CONFIG,
        )
        self.assignment_search = StructuredSearchService(
            database=self.database,
            config=ASSIGNMENT_SEARCH_CONFIG,
        )

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
            searches={
                "incidents": self.incident_search,
                "assignments": self.assignment_search,
            },
            semantic_search=self.semantic_search,
            default_semantic_limit=settings.semantic_default_limit,
        )

        self.analytics = AnalyticsSqlService(self.database)

        self.imports = ImportService(
            incident_repository=self.incidents,
            assignment_repository=self.assignments,
            vector_indexing=self.vector_indexing,
            index_batch_size=settings.import_index_batch_size,
        )

    async def start(self) -> None:
        if self.started:
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

            self._cleanup_task = asyncio.create_task(
                run_search_result_cleanup_forever(
                    self.search_results,
                    interval_seconds=self.settings.cleanup_interval_seconds,
                ),
                name="memory-search-result-cleanup",
            )
            self.started = True
        except Exception:
            logger.exception("MemoryApplication startup failed")
            await self.stop()
            raise

    async def stop(self) -> None:
        cleanup_task = self._cleanup_task
        self._cleanup_task = None

        if cleanup_task is not None:
            cleanup_task.cancel()

            with suppress(asyncio.CancelledError):
                await cleanup_task

        await self.database.close()
        self.started = False

    async def import_json(
        self,
        *,
        entity: ImportEntity,
        content: bytes,
        max_errors: int = 100,
    ) -> ImportReport:
        report = await self.imports.import_bytes(
            entity=entity,
            content=content,
            max_errors=max_errors,
        )

        await self._refresh_catalog_after_import(
            entity=entity,
            report=report,
            max_errors=max_errors,
        )

        return report

    async def import_data(
        self,
        *,
        entity: ImportEntity,
        raw: Any,
        max_errors: int = 100,
    ) -> ImportReport:
        report = await self.imports.import_data(
            entity=entity,
            raw=raw,
            max_errors=max_errors,
        )

        await self._refresh_catalog_after_import(
            entity=entity,
            report=report,
            max_errors=max_errors,
        )

        return report

    async def _refresh_catalog_after_import(
        self,
        *,
        entity: ImportEntity,
        report: ImportReport,
        max_errors: int,
    ) -> None:
        if entity != "incidents" or report.imported_count == 0:
            return

        try:
            await self.rebuild_entity_catalog()
        except Exception as exc:
            logger.exception("Entity catalog rebuild after import failed")
            _append_import_warning(
                report=report,
                max_errors=max_errors,
                code="catalog_rebuild_failed",
                message=(
                    "Entity catalog rebuild failed: "
                    f"{_safe_error_message(exc)}"
                ),
            )

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

        if row is None:
            raise RuntimeError("Healthcheck query returned no row")

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
            "presentations_count": int(row["presentations_count"]),
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
        if not self.started:
            raise RuntimeError(
                "MemoryApplication must be started before vector rebuild"
            )

        if entity not in ("incidents", "assignments", "all"):
            raise ValueError(f"Unsupported vector entity: {entity!r}")

        result: dict[str, int] = {}

        if entity in ("incidents", "all"):
            result["incidents"] = (
                await self.vector_backfill.backfill_incidents(
                    batch_size=batch_size
                )
            )

        if entity in ("assignments", "all"):
            result["assignments"] = (
                await self.vector_backfill.backfill_assignments(
                    batch_size=batch_size
                )
            )

        logger.info(
            "Vector rebuild completed: entity=%s result=%s",
            entity,
            result,
        )
        return result

    async def rebuild_entity_catalog(self) -> dict[str, int]:
        if not self.started:
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
                  AND TRIM({column_name}) != ''
                """
            )
            rows = await cursor.fetchall()

            result[entity_type] = (
                await self.entity_catalog.replace_values(
                    entity_type=entity_type,
                    values=(row["value"] for row in rows),
                )
            )

        await self.entity_resolver.refresh()

        logger.info("Entity catalog rebuild completed: %s", result)
        return result


def _append_import_warning(
    *,
    report: ImportReport,
    max_errors: int,
    code: str,
    message: str,
) -> None:
    if len(report.warnings) >= max_errors:
        return

    report.warnings.append(
        ImportErrorItem(
            index=None,
            code=code,
            message=message[:2_000],
        )
    )

    if report.status == "completed":
        report.status = "completed_with_errors"


def _safe_error_message(exc: Exception) -> str:
    return (str(exc).strip() or type(exc).__name__)[:2_000]