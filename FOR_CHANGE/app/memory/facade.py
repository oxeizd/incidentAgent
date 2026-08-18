from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memory.application import MemoryApplication
from memory.artifacts.presentations.contracts import PresentationRecord
from memory.artifacts.presentations.document import PresentationDocument
from memory.artifacts.search_results.api import (
    SearchResultNotFoundError,
    get_search_result_table_page,
)
from memory.search.queries import AssignmentSearchQuery, IncidentSearchQuery
from memory.search.service import ThreadOwnershipError


class MemoryFacadeError(RuntimeError):
    """Base error for memory facade operations."""


class MemoryAccessError(MemoryFacadeError):
    """Requested thread or search result is inaccessible for the current user."""


class PresentationOwnershipError(MemoryFacadeError):
    """Requested presentation is inaccessible for the current user."""


@dataclass(frozen=True, slots=True)
class MemoryFacade:
    """
    Application-facing memory API.

    HTTP handlers, LangGraph tools and agents call this facade instead of
    repositories, vector storage or low-level search services.
    """

    application: MemoryApplication

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

    async def create_presentation(
        self,
        *,
        user_id: str,
        thread_id: str,
        fields: PresentationDocument,
    ) -> str:
        owns_thread = await self.application.threads.thread_belongs_to_user(
            thread_id=thread_id,
            user_id=user_id,
        )

        if not owns_thread:
            raise ThreadOwnershipError(
                f"Thread {thread_id!r} does not belong to user {user_id!r}"
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

        is_owner = record.owner_user_id == user_id
        is_published = record.status == "published"

        if not is_owner and not is_published:
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