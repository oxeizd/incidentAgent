from __future__ import annotations

from datetime import datetime

from memory.artifacts.search_results.repository import SearchResultRepository
from memory.artifacts.threads.repository import ThreadRepository
from memory.search.contracts import (
    SearchExecution,
    SearchPreview,
    SearchResultItemRef,
    SearchResultReferenceArtifact,
)
from memory.search.markdown import render_search_preview_markdown
from memory.search.profiles import get_display_profile
from memory.search.projection import project_row
from memory.search.service import ThreadOwnershipError
from memory.vectors.semantic_search import SemanticSearchService


_SEMANTIC_PROFILES: dict[str, tuple[str, str]] = {
    "incidents": (
        "incidents.chat_preview.v1",
        "incidents.table.v1",
    ),
    "assignments": (
        "assignments.chat_preview.v1",
        "assignments.table.v1",
    ),
}


class SemanticSearchOrchestrationService:
    """
    Persist and post semantic-similarity results.

    SQL structured search lives in SearchOrchestrationService.
    This service handles only vector similarity results:
      - incidents: ai_description / reason_inc
      - assignments: assignment text
    """

    def __init__(
        self,
        *,
        semantic_search: SemanticSearchService,
        search_result_repository: SearchResultRepository,
        thread_repository: ThreadRepository,
        default_preview_limit: int,
        default_similarity_limit: int = 100,
    ) -> None:
        if default_preview_limit < 1:
            raise ValueError("default_preview_limit must be at least 1")

        if default_similarity_limit < 1:
            raise ValueError("default_similarity_limit must be at least 1")

        self._semantic_search = semantic_search
        self._search_result_repository = search_result_repository
        self._thread_repository = thread_repository
        self._default_preview_limit = default_preview_limit
        self._default_similarity_limit = default_similarity_limit

    async def similar_incidents_and_post(
        self,
        *,
        query_text: str,
        owner_user_id: str,
        thread_id: str,
        limit: int | None = None,
        preview_limit: int | None = None,
        now: datetime | None = None,
    ) -> str:
        execution = await self._semantic_search.similar_incidents(
            query_text=query_text,
            limit=self._resolve_similarity_limit(limit),
        )

        return await self._persist_and_post(
            execution=execution,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
            now=now,
        )

    async def similar_assignments_and_post(
        self,
        *,
        query_text: str,
        owner_user_id: str,
        thread_id: str,
        limit: int | None = None,
        preview_limit: int | None = None,
        now: datetime | None = None,
    ) -> str:
        execution = await self._semantic_search.similar_assignments(
            query_text=query_text,
            limit=self._resolve_similarity_limit(limit),
        )

        return await self._persist_and_post(
            execution=execution,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
            now=now,
        )

    async def _persist_and_post(
        self,
        *,
        execution: SearchExecution,
        owner_user_id: str,
        thread_id: str,
        preview_limit: int | None,
        now: datetime | None,
    ) -> str:
        await self._require_thread_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )

        preview_profile_name, table_profile_name = _SEMANTIC_PROFILES[
            execution.entity
        ]
        preview_schema = get_display_profile(preview_profile_name)
        table_schema = get_display_profile(table_profile_name)

        result = await self._search_result_repository.create(
            owner_user_id=owner_user_id,
            source_thread_id=thread_id,
            entity=execution.entity,
            query=execution.normalized_query,
            display=table_schema,
            items=[
                SearchResultItemRef(
                    position=index,
                    entity_id=hit.entity_id,
                    score=hit.score,
                )
                for index, hit in enumerate(execution.hits)
            ],
            now=now,
        )

        preview_size = self._resolve_preview_limit(preview_limit)
        preview_rows = [
            project_row(
                entity_id=hit.entity_id,
                payload=hit.payload,
                schema=preview_schema,
            )
            for hit in execution.hits[:preview_size]
        ]

        artifact = SearchResultReferenceArtifact(
            result_id=result.id,
            entity=execution.entity,
            total_count=result.total_count,
            preview_count=len(preview_rows),
            display=preview_schema,
            preview=SearchPreview(rows=preview_rows),
            created_at=result.created_at,
            expires_at=result.expires_at,
        )

        markdown = render_search_preview_markdown(artifact)

        return await self._thread_repository.add_message(
            thread_id=thread_id,
            role="assistant",
            content=markdown,
            artifact=artifact.model_dump(mode="json"),
        )

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

    def _resolve_preview_limit(self, requested: int | None) -> int:
        if requested is None:
            return self._default_preview_limit

        if requested < 1:
            raise ValueError("preview_limit must be at least 1")

        return min(requested, 20)

    def _resolve_similarity_limit(self, requested: int | None) -> int:
        if requested is None:
            return self._default_similarity_limit

        if requested < 1:
            raise ValueError("limit must be at least 1")

        return min(requested, 1_000)