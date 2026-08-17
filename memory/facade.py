from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memory.application import MemoryApplication
from memory.artifacts.search_results.api import (
    SearchResultNotFoundError,
    get_search_result_table_page,
)
from memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
)


class MemoryFacadeError(RuntimeError):
    """Base error for memory facade operations."""


class MemoryAccessError(MemoryFacadeError):
    """Requested thread/result is inaccessible for the current user."""


@dataclass(frozen=True, slots=True)
class MemoryFacade:
    """
    Application-facing memory API.

    Your LangGraph tools, HTTP handlers, or agents should call this facade,
    not repositories, vector storage, or low-level search services directly.
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

    async def healthcheck(self) -> dict[str, object]:
        return await self.application.healthcheck()