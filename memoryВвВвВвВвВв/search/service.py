from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.memory.search.contracts import (
    EntityType,
    SearchExecution,
    SearchResultReferenceArtifact,
)
from app.memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
)
from app.memory.search.result_writer import SearchResultWriter
from app.memory.search.structured import (
    SearchSort,
    StructuredSearchService,
)
from app.memory.vectors.semantic_search import SemanticSearchService


StructuredQuery = IncidentSearchQuery | AssignmentSearchQuery


class SearchOrchestrationService:
    """
    Один search use-case для structured, semantic и hybrid поиска.

    - filters без semantic_query:
      обычный typed SQL search.

    - semantic_query без filters:
      глобальный vector KNN search.

    - filters + semantic_query:
      structured SQL определяет допустимые записи, затем semantic ranking
      выполняется только внутри этого набора.

    UI и агент не выбирают отдельный "semantic mode": они передают то,
    что извлекли из запроса пользователя.
    """

    def __init__(
        self,
        *,
        result_writer: SearchResultWriter,
        searches: Mapping[EntityType, StructuredSearchService],
        semantic_search: SemanticSearchService,
        default_semantic_limit: int = 100,
    ) -> None:
        if default_semantic_limit < 1:
            raise ValueError(
                "default_semantic_limit must be at least 1"
            )

        required_entities: set[EntityType] = {
            "incidents",
            "assignments",
        }
        missing = required_entities - set(searches)

        if missing:
            raise ValueError(
                f"Missing structured search services: {sorted(missing)}"
            )

        self._result_writer = result_writer
        self._searches = dict(searches)
        self._semantic_search = semantic_search
        self._default_semantic_limit = default_semantic_limit

    async def search(
        self,
        *,
        entity: EntityType,
        owner_user_id: str,
        thread_id: str,
        filters: dict[str, Any] | None = None,
        semantic_query: str | None = None,
        sorts: Sequence[SearchSort] | None = None,
        top_n: int | None = None,
        preview_limit: int | None = None,
    ) -> SearchResultReferenceArtifact:
        execution = await self.find_execution(
            entity=entity,
            filters=filters,
            semantic_query=semantic_query,
            sorts=sorts,
            top_n=top_n,
        )

        return await self._result_writer.write_execution(
            execution=execution,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            preview_limit=preview_limit,
        )

    async def find_execution(
        self,
        *,
        entity: EntityType,
        filters: dict[str, Any] | None = None,
        semantic_query: str | None = None,
        sorts: Sequence[SearchSort] | None = None,
        top_n: int | None = None,
    ) -> SearchExecution:
        normalized_filters = _normalize_filters(filters)
        normalized_semantic_query = _normalize_semantic_query(
            semantic_query
        )

        if normalized_semantic_query is None:
            if not normalized_filters:
                raise ValueError(
                    "Search requires filters or semantic_query"
                )

            return await self._structured_execution(
                entity=entity,
                filters=normalized_filters,
                sorts=sorts,
                top_n=top_n,
            )

        return await self._semantic_execution(
            entity=entity,
            filters=normalized_filters,
            semantic_query=normalized_semantic_query,
            top_n=top_n,
        )

    async def _structured_execution(
        self,
        *,
        entity: EntityType,
        filters: dict[str, Any],
        sorts: Sequence[SearchSort] | None,
        top_n: int | None,
    ) -> SearchExecution:
        query = _parse_structured_query(
            entity=entity,
            filters=filters,
        )

        return await self._searches[entity].execute(
            query,
            sorts=sorts,
            top_n=top_n,
        )

    async def _semantic_execution(
        self,
        *,
        entity: EntityType,
        filters: dict[str, Any],
        semantic_query: str,
        top_n: int | None,
    ) -> SearchExecution:
        allowed_ids: set[str] | None = None

        if filters:
            query = _parse_structured_query(
                entity=entity,
                filters=filters,
            )
            structured_execution = await self._searches[entity].execute(
                query
            )
            allowed_ids = {
                hit.entity_id
                for hit in structured_execution.hits
            }

        return await self._semantic_search.search(
            entity=entity,
            query_text=semantic_query,
            limit=self._resolve_semantic_limit(top_n),
            allowed_ids=allowed_ids,
        )

    def _resolve_semantic_limit(
        self,
        requested: int | None,
    ) -> int:
        if requested is None:
            return self._default_semantic_limit

        if requested < 1:
            raise ValueError("top_n must be at least 1")

        return min(requested, 500)


def _parse_structured_query(
    *,
    entity: EntityType,
    filters: dict[str, Any],
) -> StructuredQuery:
    if entity == "incidents":
        return IncidentSearchQuery.model_validate(filters)

    if entity == "assignments":
        return AssignmentSearchQuery.model_validate(filters)

    raise ValueError(f"Unsupported search entity: {entity!r}")


def _normalize_filters(
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError("filters must be an object")

    return {
        key: item
        for key, item in value.items()
        if item is not None
    }


def _normalize_semantic_query(value: str | None) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("semantic_query must be a string")

    normalized = value.strip()
    return normalized or None