from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from memory.artifacts.search_results.repository import SearchResultRepository
from memory.artifacts.threads.repository import ThreadRepository
from memory.search.contracts import (
    EntityType,
    SearchExecution,
    SearchHit,
    SearchPreview,
    SearchResultItemRef,
    SearchResultReferenceArtifact,
)
from memory.search.markdown import render_search_preview_markdown
from memory.search.profiles import get_display_profile
from memory.search.projection import project_row
from memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
    SearchQuery,
)
from memory.search.streaming import validate_batch_size


class SupportsSearch(Protocol):
    async def execute(self, query: SearchQuery) -> SearchExecution: ...


class SupportsStreamingSearch(Protocol):
    async def stream(
        self,
        query: SearchQuery,
        *,
        batch_size: int,
    ) -> AsyncIterator[list[SearchHit]]: ...


class ThreadOwnershipError(PermissionError):
    """The requested thread is absent or belongs to another user."""


_PROFILE_BY_ENTITY: dict[EntityType, tuple[str, str]] = {
    "incidents": ("incidents.chat_preview.v1", "incidents.table.v1"),
    "assignments": ("assignments.chat_preview.v1", "assignments.table.v1"),
}


class SearchOrchestrationService:
    """One public use-case for persisted incident and assignment search."""

    def __init__(
        self,
        *,
        search_result_repository: SearchResultRepository,
        thread_repository: ThreadRepository,
        incident_search: SupportsStreamingSearch,
        assignment_search: SupportsStreamingSearch,
        default_preview_limit: int,
        default_batch_size: int = 500,
    ) -> None:
        if default_preview_limit < 1:
            raise ValueError("default_preview_limit must be at least 1")

        self._search_result_repository = search_result_repository
        self._thread_repository = thread_repository
        self._incident_search = incident_search
        self._assignment_search = assignment_search
        self._default_preview_limit = default_preview_limit
        self._default_batch_size = validate_batch_size(default_batch_size)

    async def search_and_post(
        self,
        *,
        entity: EntityType,
        query: SearchQuery,
        owner_user_id: str,
        thread_id: str,
        preview_limit: int | None = None,
        batch_size: int | None = None,
        now: datetime | None = None,
    ) -> str:
        """
        Run a complete persisted search.

        All matching IDs and search order are retained for seven days.
        The associated assistant message contains only a bounded preview.
        """
        searcher = self._resolve_searcher(entity=entity, query=query)

        return await self._stream_and_post(
            searcher=searcher,
            entity=entity,
            query=query,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
            batch_size=(
                self._default_batch_size
                if batch_size is None
                else validate_batch_size(batch_size)
            ),
            now=now,
        )

    async def _stream_and_post(
        self,
        *,
        searcher: SupportsStreamingSearch,
        entity: EntityType,
        query: SearchQuery,
        owner_user_id: str,
        thread_id: str,
        preview_limit: int | None,
        batch_size: int,
        now: datetime | None,
    ) -> str:
        await self._require_thread_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )

        preview_size = self._resolve_preview_limit(preview_limit)
        preview_profile_name, table_profile_name = _PROFILE_BY_ENTITY[entity]

        preview_schema = get_display_profile(preview_profile_name)
        table_schema = get_display_profile(table_profile_name)

        result = await self._search_result_repository.create_building(
            owner_user_id=owner_user_id,
            source_thread_id=thread_id,
            entity=entity,
            query=query.to_normalized_dict(),
            display=table_schema,
            now=now,
        )

        preview_rows = []
        position = 0

        try:
            async for hit_batch in searcher.stream(
                query,
                batch_size=batch_size,
            ):
                item_refs = [
                    SearchResultItemRef(
                        position=position + index,
                        entity_id=hit.entity_id,
                        score=hit.score,
                    )
                    for index, hit in enumerate(hit_batch)
                ]

                await self._search_result_repository.append_items(
                    result_id=result.id,
                    items=item_refs,
                )

                remaining_preview_slots = preview_size - len(preview_rows)
                if remaining_preview_slots > 0:
                    preview_rows.extend(
                        project_row(
                            entity_id=hit.entity_id,
                            payload=hit.payload,
                            schema=preview_schema,
                        )
                        for hit in hit_batch[:remaining_preview_slots]
                    )

                position += len(hit_batch)

            await self._search_result_repository.mark_ready(
                result_id=result.id,
            )
        except Exception:
            await self._search_result_repository.mark_failed(
                result_id=result.id,
            )
            raise

        artifact = SearchResultReferenceArtifact(
            result_id=result.id,
            entity=entity,
            total_count=position,
            preview_count=len(preview_rows),
            display=preview_schema,
            preview=SearchPreview(rows=preview_rows),
            created_at=result.created_at,
            expires_at=result.expires_at,
        )

        return await self._post_artifact(
            thread_id=thread_id,
            artifact=artifact,
        )

    def _resolve_searcher(
        self,
        *,
        entity: EntityType,
        query: SearchQuery,
    ) -> SupportsStreamingSearch:
        if entity == "incidents":
            if not isinstance(query, IncidentSearchQuery):
                raise TypeError(
                    "Incident search requires IncidentSearchQuery"
                )
            return self._incident_search

        if not isinstance(query, AssignmentSearchQuery):
            raise TypeError(
                "Assignment search requires AssignmentSearchQuery"
            )
        return self._assignment_search

    async def _require_thread_owner(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
    ) -> None:
        belongs_to_user = await self._thread_repository.thread_belongs_to_user(
            thread_id=thread_id,
            user_id=owner_user_id,
        )
        if not belongs_to_user:
            raise ThreadOwnershipError(
                f"Thread {thread_id!r} does not belong to current user"
            )

    async def _post_artifact(
        self,
        *,
        thread_id: str,
        artifact: SearchResultReferenceArtifact,
    ) -> str:
        markdown = render_search_preview_markdown(artifact)

        return await self._thread_repository.add_message(
            thread_id=thread_id,
            role="assistant",
            content=markdown,
            artifact=artifact.model_dump(mode="json"),
        )

    def _resolve_preview_limit(self, requested: int | None) -> int:
        if requested is None:
            return self._default_preview_limit

        if requested < 1:
            raise ValueError("preview_limit must be at least 1")

        return min(requested, 20)