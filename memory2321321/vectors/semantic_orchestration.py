from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.memory.artifacts.assignments.search import AssignmentSearch
from app.memory.artifacts.incidents.search import IncidentSearch
from app.memory.search.contracts import SearchExecution
from app.memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
)
from app.memory.search.result_writer import SearchResultWriter
from app.memory.vectors.semantic_search import (
    SemanticSearchService,
)


SemanticEntity = Literal["incidents", "assignments"]


@dataclass(frozen=True, slots=True)
class SemanticExecutionConfig:
    """
    Static entity configuration for semantic orchestration.

    Query validators and execution functions are selected by backend code,
    never from user input or LLM-generated SQL.
    """

    entity: SemanticEntity
    query_factory: Callable[[dict[str, Any]], Any]
    structured_execute: Callable[..., Awaitable[SearchExecution]]
    semantic_execute: Callable[..., Awaitable[SearchExecution]]


class SemanticSearchOrchestrationService:
    """
    Semantic-primary and hybrid semantic+filters retrieval.

    Responsibilities:
    - validates optional structured filters;
    - converts filters to allowed entity IDs;
    - invokes semantic engine;
    - writes persisted results through SearchResultWriter.

    Snapshot persistence, ownership validation and preview construction
    are delegated to SearchResultWriter.
    """

    def __init__(
        self,
        *,
        semantic_search: SemanticSearchService,
        incident_search: IncidentSearch,
        assignment_search: AssignmentSearch,
        result_writer: SearchResultWriter,
        default_similarity_limit: int = 100,
    ) -> None:
        if default_similarity_limit < 1:
            raise ValueError(
                "default_similarity_limit must be at least 1"
            )

        self._result_writer = result_writer
        self._default_similarity_limit = default_similarity_limit

        self._entities: dict[
            SemanticEntity,
            SemanticExecutionConfig,
        ] = {
            "incidents": SemanticExecutionConfig(
                entity="incidents",
                query_factory=IncidentSearchQuery.model_validate,
                structured_execute=incident_search.execute,
                semantic_execute=semantic_search.similar_incidents,
            ),
            "assignments": SemanticExecutionConfig(
                entity="assignments",
                query_factory=AssignmentSearchQuery.model_validate,
                structured_execute=assignment_search.execute,
                semantic_execute=semantic_search.similar_assignments,
            ),
        }

    async def similar_incidents(
        self,
        *,
        query_text: str,
        owner_user_id: str,
        thread_id: str,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        preview_limit: int | None = None,
    ) -> Any:
        return await self._similar(
            entity="incidents",
            query_text=query_text,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            filters=filters,
            limit=limit,
            preview_limit=preview_limit,
        )

    async def similar_assignments(
        self,
        *,
        query_text: str,
        owner_user_id: str,
        thread_id: str,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        preview_limit: int | None = None,
    ) -> Any:
        return await self._similar(
            entity="assignments",
            query_text=query_text,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            filters=filters,
            limit=limit,
            preview_limit=preview_limit,
        )

    async def find_similar_incidents_execution(
        self,
        *,
        query_text: str,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> SearchExecution:
        return await self._find_execution(
            entity="incidents",
            query_text=query_text,
            filters=filters,
            limit=limit,
        )

    async def find_similar_assignments_execution(
        self,
        *,
        query_text: str,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> SearchExecution:
        return await self._find_execution(
            entity="assignments",
            query_text=query_text,
            filters=filters,
            limit=limit,
        )

    async def _similar(
        self,
        *,
        entity: SemanticEntity,
        query_text: str,
        owner_user_id: str,
        thread_id: str,
        filters: dict[str, Any] | None,
        limit: int | None,
        preview_limit: int | None,
    ) -> Any:
        execution = await self._find_execution(
            entity=entity,
            query_text=query_text,
            filters=filters,
            limit=limit,
        )

        return await self._result_writer.write_execution(
            execution=execution,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
        )

    async def _find_execution(
        self,
        *,
        entity: SemanticEntity,
        query_text: str,
        filters: dict[str, Any] | None,
        limit: int | None,
    ) -> SearchExecution:
        config = self._entities[entity]
        allowed_ids: set[str] | None = None

        if filters:
            structured_query = config.query_factory(filters)
            filtered = await config.structured_execute(
                structured_query
            )
            allowed_ids = {
                hit.entity_id
                for hit in filtered.hits
            }

        return await config.semantic_execute(
            query_text=query_text,
            limit=self._resolve_similarity_limit(limit),
            allowed_ids=allowed_ids,
        )

    def _resolve_similarity_limit(
        self,
        requested: int | None,
    ) -> int:
        if requested is None:
            return self._default_similarity_limit

        if requested < 1:
            raise ValueError("limit must be at least 1")

        return min(requested, 500)