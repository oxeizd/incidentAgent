from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.memory.analytics.contracts import (
    AnalyticsSqlRequest,
    AnalyticsSqlResult,
)
from app.memory.application import MemoryApplication
from app.memory.artifacts.incident_reports.document import (
    IncidentReportRecord,
    IncidentReportStatus,
)
from app.memory.artifacts.incident_reports.errors import (
    IncidentReportVersionConflictError,
)
from app.memory.artifacts.presentations.contracts import (
    PresentationRecord,
)
from app.memory.artifacts.presentations.document import (
    PresentationDocument,
)
from app.memory.artifacts.search_results.page  import (
    SearchResultNotFoundError,
    get_search_result_table_page,
)
from app.memory.artifacts.entity_catalog.contracts import (
    EntityCatalogEntry,
    EntityCatalogType,
    EntityLookupResult,
)
from app.memory.errors import ThreadOwnershipError
from app.memory.artifacts.imports_service.contracts import (
    ImportEntity,
    ImportReport,
)
from app.memory.search.contracts import (
    EntityType,
    SearchResultReferenceArtifact,
)
from app.memory.search.structured import SearchSort


class MemoryFacadeError(RuntimeError):
    """Базовая ошибка публичной границы memory."""


class MemoryAccessError(MemoryFacadeError):
    """Ресурс отсутствует либо недоступен текущему пользователю."""


class PresentationOwnershipError(MemoryFacadeError):
    """Презентация отсутствует либо недоступна текущему пользователю."""


class IncidentReportOwnershipError(MemoryFacadeError):
    """RCA-справка отсутствует либо недоступна текущему пользователю."""


@dataclass(frozen=True, slots=True)
class MemoryFacade:
    """
    Единственная public boundary memory.

    HTTP, agent tools и workflow-код не используют repositories, Database
    и внутренние service instances напрямую.
    """

    application: MemoryApplication

    # ── Threads ──────────────────────────────────────────────────────

    async def create_thread_for_user(
        self,
        *,
        user_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        return await self.application.threads.create_thread(
            user_id=user_id,
            title=title,
        )

    async def list_threads_for_user(
        self,
        *,
        user_id: str,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await self.application.threads.list_threads(
            user_id=user_id,
            cursor_value=cursor,
            limit=limit,
        )

    async def get_thread_for_user(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        thread = await self.application.threads.get_thread(
            thread_id=thread_id,
        )

        if thread is None or thread["user_id"] != user_id:
            raise MemoryAccessError(
                "Thread was not found or is not available to this user"
            )

        return {
            "id": str(thread["id"]),
            "title": thread.get("title"),
            "created_at": str(thread["created_at"]),
            "updated_at": str(thread["updated_at"]),
        }

    async def get_thread_messages_for_user(
        self,
        *,
        user_id: str,
        thread_id: str,
        before: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        await self._require_thread_owner(
            user_id=user_id,
            thread_id=thread_id,
        )

        return await self.application.threads.get_messages(
            thread_id=thread_id,
            before=before,
            limit=limit,
        )

    async def add_thread_message_for_user(
        self,
        *,
        user_id: str,
        thread_id: str,
        role: str,
        content: str,
        artifact: dict[str, Any] | None = None,
    ) -> str:
        await self._require_thread_owner(
            user_id=user_id,
            thread_id=thread_id,
        )

        return await self.application.threads.add_message(
            thread_id=thread_id,
            role=role,
            content=content,
            artifact=artifact,
        )

    async def delete_thread_for_user(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> bool:
        return await self.application.threads.delete_thread(
            thread_id=thread_id,
            user_id=user_id,
        )

    async def thread_belongs_to_user(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> bool:
        return await self.application.threads.thread_belongs_to_user(
            user_id=user_id,
            thread_id=thread_id,
        )

    async def get_thread_owner(
        self,
        *,
        thread_id: str,
    ) -> str | None:
        return await self.application.threads.get_thread_owner(
            thread_id=thread_id,
        )

    # ── Domain reads ─────────────────────────────────────────────────

    async def get_incident(
        self,
        *,
        number: str,
    ) -> dict[str, Any] | None:
        return await self.application.incidents.get(number)

    async def get_assignment(
        self,
        *,
        assignment_id: str,
    ) -> dict[str, Any] | None:
        return await self.application.assignments.get(assignment_id)

    # ── Entity catalog ───────────────────────────────────────────────

    async def lookup_entities(
        self,
        *,
        query: str,
        entity_type: EntityCatalogType | None = None,
        limit: int = 5,
    ) -> EntityLookupResult:
        return await self.application.entity_resolver.lookup(
            query=query,
            entity_type=entity_type,
            limit=limit,
        )

    async def list_catalog_entries(
        self,
        *,
        entity_type: EntityCatalogType,
        limit: int | None = None,
    ) -> list[EntityCatalogEntry]:
        return await self.application.entity_catalog.list_by_type(
            entity_type=entity_type,
            limit=limit,
        )

    async def list_all_catalog_entries(
        self,
    ) -> list[EntityCatalogEntry]:
        return await self.application.entity_catalog.list_all()

    async def upsert_catalog_entry(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
        aliases: list[str] | None = None,
        source_count: int = 0,
    ) -> EntityCatalogEntry:
        entry = await self.application.entity_catalog.upsert(
            entity_type=entity_type,
            canonical_value=canonical_value,
            aliases=aliases,
            source_count=source_count,
        )
        await self.application.entity_resolver.refresh()
        return entry

    async def delete_catalog_entry(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> bool:
        deleted = await self.application.entity_catalog.delete(
            entity_type=entity_type,
            canonical_value=canonical_value,
        )

        if deleted:
            await self.application.entity_resolver.refresh()

        return deleted

    async def rebuild_entity_catalog(self) -> dict[str, int]:
        return await self.application.rebuild_entity_catalog()

    # ── Search ───────────────────────────────────────────────────────

    async def search(
        self,
        *,
        entity: EntityType,
        user_id: str,
        thread_id: str,
        filters: dict[str, Any] | None = None,
        semantic_query: str | None = None,
        sorts: list[dict[str, str]] | None = None,
        top_n: int | None = None,
        preview_limit: int | None = None,
    ) -> SearchResultReferenceArtifact:
        await self._require_thread_owner(
            user_id=user_id,
            thread_id=thread_id,
        )

        typed_sorts = [
            SearchSort.model_validate(item)
            for item in sorts or []
        ]

        return await self.application.search.search(
            entity=entity,
            owner_user_id=user_id,
            thread_id=thread_id,
            filters=filters,
            semantic_query=semantic_query,
            sorts=typed_sorts,
            top_n=top_n,
            preview_limit=preview_limit,
        )

    async def query_analytics_sql(
        self,
        *,
        sql: str,
        parameters: list[str | int | float | bool | None] | None = None,
        max_rows: int = 100,
    ) -> AnalyticsSqlResult:
        """
        Read-only analytics tool for the AI agent.

        SQL can read only analytics_incidents and analytics_assignments.
        The method deliberately does not take user_id/thread_id: analytic
        rows are domain data, while conversation data is never queryable
        through this tool.
        """
        return await self.application.analytics.execute(
            AnalyticsSqlRequest(
                sql=sql,
                parameters=parameters or [],
                max_rows=max_rows,
            )
        )

    async def open_search_result(
        self,
        *,
        user_id: str,
        result_id: str,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            return await get_search_result_table_page(
                search_result_repository=self.application.search_results,
                incident_repository=self.application.incidents,
                assignment_repository=self.application.assignments,
                result_id=result_id,
                owner_user_id=user_id,
                cursor=cursor,
                limit=limit,
            )
        except SearchResultNotFoundError as exc:
            raise MemoryAccessError(
                "Search result was not found, has expired, "
                "or does not belong to the current user"
            ) from exc

    async def invalidate_search_result(
        self,
        *,
        user_id: str,
        result_id: str,
    ) -> bool:
        return await self.application.search_results.invalidate(
            result_id=result_id,
            owner_user_id=user_id,
        )

    # ── Incident reports ─────────────────────────────────────────────

    async def create_incident_report(
        self,
        *,
        user_id: str,
        thread_id: str,
        sections: dict[str, Any],
        created_by_task_id: str,
        status: IncidentReportStatus = "draft",
    ) -> IncidentReportRecord:
        await self._require_thread_owner(
            user_id=user_id,
            thread_id=thread_id,
        )

        return await self.application.incident_reports.create(
            owner_user_id=user_id,
            thread_id=thread_id,
            sections=sections,
            created_by_task_id=created_by_task_id,
            status=status,
        )

    async def get_incident_report_for_user(
        self,
        *,
        user_id: str,
        report_id: str,
    ) -> IncidentReportRecord:
        report = await self.application.incident_reports.get(report_id)

        if report is None or report.owner_user_id != user_id:
            raise IncidentReportOwnershipError(
                "Incident report not found or unavailable"
            )

        return report

    async def get_incident_report_for_agent(
        self,
        *,
        user_id: str,
        thread_id: str,
        report_id: str,
    ) -> IncidentReportRecord:
        report = await self.get_incident_report_for_user(
            user_id=user_id,
            report_id=report_id,
        )

        if report.thread_id != thread_id:
            raise IncidentReportOwnershipError(
                "Incident report is not available in this thread"
            )

        return report

    async def list_incident_reports_for_user(
        self,
        *,
        user_id: str,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[IncidentReportRecord]:
        return await self.application.incident_reports.list_mine(
            owner_user_id=user_id,
            thread_id=thread_id,
            limit=limit,
        )

    async def append_incident_report_version(
        self,
        *,
        user_id: str,
        thread_id: str,
        report_id: str,
        expected_version: int,
        sections: dict[str, Any],
        created_by_task_id: str,
        note: str | None = None,
        status: IncidentReportStatus | None = None,
    ) -> IncidentReportRecord:
        await self.get_incident_report_for_agent(
            user_id=user_id,
            thread_id=thread_id,
            report_id=report_id,
        )

        try:
            return await self.application.incident_reports.append_version(
                report_id=report_id,
                expected_version=expected_version,
                sections=sections,
                created_by_task_id=created_by_task_id,
                note=note,
                status=status,
            )
        except IncidentReportVersionConflictError:
            raise
        except KeyError as exc:
            raise IncidentReportOwnershipError(
                "Incident report not found or unavailable"
            ) from exc

    # ── Presentations ────────────────────────────────────────────────

    async def create_presentation(
        self,
        *,
        user_id: str,
        thread_id: str,
        fields: PresentationDocument,
    ) -> str:
        await self._require_thread_owner(
            user_id=user_id,
            thread_id=thread_id,
        )

        return await self.application.presentations.create(
            owner_user_id=user_id,
            thread_id=thread_id,
            fields=fields,
        )

    async def get_visible_presentation(
        self,
        *,
        user_id: str,
        presentation_id: str,
    ) -> PresentationRecord:
        record = await self.application.presentations.get(
            presentation_id,
        )

        if record is None:
            raise PresentationOwnershipError("Presentation not found")

        if (
            record.owner_user_id != user_id
            and record.status != "published"
        ):
            raise PresentationOwnershipError(
                "This draft belongs to another user"
            )

        return record

    async def list_my_presentations(
        self,
        user_id: str,
    ) -> list[PresentationRecord]:
        return await self.application.presentations.list_mine(user_id)

    async def list_shared_presentations(
        self,
    ) -> list[PresentationRecord]:
        return await self.application.presentations.list_shared()

    async def update_presentation_fields(
        self,
        *,
        user_id: str,
        presentation_id: str,
        fields: PresentationDocument,
    ) -> bool:
        return await self.application.presentations.update_fields(
            presentation_id,
            user_id,
            fields,
        )

    async def publish_presentation(
        self,
        *,
        user_id: str,
        presentation_id: str,
    ) -> bool:
        return await self.application.presentations.publish(
            presentation_id,
            user_id,
        )

    async def unpublish_presentation(
        self,
        *,
        user_id: str,
        presentation_id: str,
    ) -> bool:
        return await self.application.presentations.unpublish(
            presentation_id,
            user_id,
        )

    async def delete_presentation(
        self,
        *,
        user_id: str,
        presentation_id: str,
    ) -> bool:
        return await self.application.presentations.delete(
            presentation_id,
            user_id,
        )

    # ── Import and maintenance ───────────────────────────────────────

    async def import_json(
        self,
        *,
        entity: ImportEntity,
        content: bytes,
        max_errors: int = 100,
    ) -> ImportReport:
        return await self.application.import_json(
            entity=entity,
            content=content,
            max_errors=max_errors,
        )

    async def import_data(
        self,
        *,
        entity: ImportEntity,
        raw: Any,
        max_errors: int = 100,
    ) -> ImportReport:
        return await self.application.import_data(
            entity=entity,
            raw=raw,
            max_errors=max_errors,
        )

    async def rebuild_vector_indexes(
        self,
        *,
        entity: Literal["incidents", "assignments", "all"] = "all",
        batch_size: int = 100,
    ) -> dict[str, int]:
        return await self.application.rebuild_vector_indexes(
            entity=entity,
            batch_size=batch_size,
        )

    async def cleanup_expired_search_results(self) -> int:
        return await self.application.search_results.cleanup_expired()

    async def healthcheck(self) -> dict[str, object]:
        return await self.application.healthcheck()

    async def _require_thread_owner(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> None:
        belongs = await self.thread_belongs_to_user(
            user_id=user_id,
            thread_id=thread_id,
        )

        if not belongs:
            raise ThreadOwnershipError(
                f"Thread {thread_id!r} does not belong to "
                f"user {user_id!r}"
            )