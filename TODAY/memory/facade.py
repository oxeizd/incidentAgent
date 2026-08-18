from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.memory.application import MemoryApplication
from app.memory.artifacts.presentations.contracts import PresentationRecord
from app.memory.artifacts.presentations.document import PresentationDocument
from app.memory.artifacts.search_results.api import (
    SearchResultNotFoundError,
    get_search_result_table_page,
)
from app.memory.search.queries import AssignmentSearchQuery, IncidentSearchQuery
from app.memory.search.service import ThreadOwnershipError


class MemoryFacadeError(RuntimeError):
    """Базовая ошибка публичного application boundary memory."""


class MemoryAccessError(MemoryFacadeError):
    """Тред, search result или другой ресурс недоступен пользователю."""


class PresentationOwnershipError(MemoryFacadeError):
    """Презентация отсутствует или недоступна текущему пользователю."""


@dataclass(frozen=True, slots=True)
class MemoryFacade:
    """
    Единственная публичная точка входа в memory.

    - UI operations могут создавать artifacts/messages.
    - `*_for_agent` методы strictly read-only.
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

    # ── Read-only retrieval for AI ───────────────────────────────────

    async def get_incident_for_agent(
        self,
        *,
        number: str,
    ) -> dict[str, Any] | None:
        """
        Возвращает один incident в ограниченном LLM-safe формате.

        RCA/creator не импортируют repository и не получают source_payload
        или лишние технические поля из SQLite row.
        """
        incident = await self.application.incidents.get(number)

        if incident is None:
            return None

        allowed_fields = (
            "number",
            "status",
            "priority_code",
            "system_name",
            "work_group",
            "element_name",
            "created_by",
            "executor_name",
            "description",
            "reason_inc",
            "solution",
            "impact",
            "start_time",
            "end_time",
            "mttd",
            "mttr",
            "downtime",
        )

        return {
            field_name: incident[field_name]
            for field_name in allowed_fields
            if incident.get(field_name) is not None
        }

    async def get_assignment_for_agent(
        self,
        *,
        assignment_id: str,
    ) -> dict[str, Any] | None:
        assignment = await self.application.assignments.get(
            assignment_id,
        )

        if assignment is None:
            return None

        allowed_fields = (
            "id",
            "incident_id",
            "ior",
            "task",
            "unit",
            "assignment",
            "responsible",
            "deadline",
            "assigned_at",
            "status",
        )

        return {
            field_name: assignment[field_name]
            for field_name in allowed_fields
            if assignment.get(field_name) is not None
        }

    async def retrieve_incidents_for_agent(
        self,
        *,
        filters: dict[str, Any],
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await self.application.agent_retrieval.retrieve_incidents(
            filters=filters,
            limit=limit,
        )

    async def retrieve_assignments_for_agent(
        self,
        *,
        filters: dict[str, Any],
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await self.application.agent_retrieval.retrieve_assignments(
            filters=filters,
            limit=limit,
        )

    async def find_similar_incidents_for_agent(
        self,
        *,
        query_text: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await self.application.agent_retrieval.find_similar_incidents(
            query_text=query_text,
            limit=limit,
        )

    async def find_similar_assignments_for_agent(
        self,
        *,
        query_text: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await self.application.agent_retrieval.find_similar_assignments(
            query_text=query_text,
            limit=limit,
        )

    # ── UI search с persisted artifacts ──────────────────────────────

    async def search_incidents(
        self,
        *,
        user_id: str,
        thread_id: str,
        filters: dict[str, Any],
        preview_limit: int | None = None,
    ) -> str:
        query = IncidentSearchQuery.model_validate(filters)

        return await self.application.search.search_and_post(
            entity="incidents",
            query=query,
            owner_user_id=user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
        )

    async def search_assignments(
        self,
        *,
        user_id: str,
        thread_id: str,
        filters: dict[str, Any],
        preview_limit: int | None = None,
    ) -> str:
        query = AssignmentSearchQuery.model_validate(filters)

        return await self.application.search.search_and_post(
            entity="assignments",
            query=query,
            owner_user_id=user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
        )

    async def find_similar_incidents(
        self,
        *,
        user_id: str,
        thread_id: str,
        query_text: str,
        limit: int | None = None,
        preview_limit: int | None = None,
    ) -> str:
        return await self.application.semantic.similar_incidents_and_post(
            query_text=query_text,
            owner_user_id=user_id,
            thread_id=thread_id,
            limit=limit,
            preview_limit=preview_limit,
        )

    async def find_similar_assignments(
        self,
        *,
        user_id: str,
        thread_id: str,
        query_text: str,
        limit: int | None = None,
        preview_limit: int | None = None,
    ) -> str:
        return await self.application.semantic.similar_assignments_and_post(
            query_text=query_text,
            owner_user_id=user_id,
            thread_id=thread_id,
            limit=limit,
            preview_limit=preview_limit,
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

    # ── Domain direct read ───────────────────────────────────────────

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
        record = await self.application.presentations.get(presentation_id)

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

    async def list_shared_presentations(self) -> list[PresentationRecord]:
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