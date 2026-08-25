from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.memory.artifacts.assignments.search import (
    AssignmentSearch,
    SearchSort as AssignmentSearchSort,
)
from app.memory.artifacts.incidents.search import (
    IncidentSearch,
    SearchSort as IncidentSearchSort,
)
from app.memory.search.contracts import (
    EntityType,
    SearchResultReferenceArtifact,
)
from app.memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
    SearchQuery,
)
from app.memory.search.result_writer import SearchResultWriter
from app.memory.search.streaming import validate_batch_size


class SearchOrchestrationService:
    """
    Structured SQL search с immutable persisted result.

    Отвечает только за выбор entity searcher, валидацию typed query/sort
    и сбор query metadata. Ownership, snapshot lifecycle и preview
    делегируются SearchResultWriter.
    """

    def __init__(
        self,
        *,
        result_writer: SearchResultWriter,
        incident_search: IncidentSearch,
        assignment_search: AssignmentSearch,
        default_batch_size: int = 500,
    ) -> None:
        self._result_writer = result_writer
        self._incident_search = incident_search
        self._assignment_search = assignment_search
        self._default_batch_size = validate_batch_size(
            default_batch_size
        )

    async def search(
        self,
        *,
        entity: EntityType,
        query: SearchQuery,
        owner_user_id: str,
        thread_id: str,
        sorts: Sequence[
            IncidentSearchSort | AssignmentSearchSort
        ] | None = None,
        top_n: int | None = None,
        allowed_ids: Sequence[str] | None = None,
        preview_limit: int | None = None,
        batch_size: int | None = None,
        now: datetime | None = None,
    ) -> SearchResultReferenceArtifact:
        if top_n is not None and top_n < 1:
            raise ValueError("top_n must be at least 1")

        searcher = self._resolve_searcher(
            entity=entity,
            query=query,
        )
        normalized_sorts = self._validate_sorts(
            entity=entity,
            sorts=sorts,
        )

        normalized_query = query.to_normalized_dict()

        if normalized_sorts:
            normalized_query["sort"] = [
                sort.model_dump(mode="json")
                for sort in normalized_sorts
            ]

        if top_n is not None:
            normalized_query["top_n"] = top_n

        if allowed_ids is not None:
            normalized_query["semantic_candidate_count"] = len(
                {
                    value.strip()
                    for value in allowed_ids
                    if isinstance(value, str) and value.strip()
                }
            )

        stream = searcher.stream(
            query,
            batch_size=(
                self._default_batch_size
                if batch_size is None
                else validate_batch_size(batch_size)
            ),
            sorts=normalized_sorts,
            top_n=top_n,
            allowed_ids=allowed_ids,
        )

        return await self._result_writer.write_stream(
            entity=entity,
            query=normalized_query,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            stream=stream,
            preview_limit=preview_limit,
            now=now,
        )

    def _resolve_searcher(
        self,
        *,
        entity: EntityType,
        query: SearchQuery,
    ) -> IncidentSearch | AssignmentSearch:
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

    def _validate_sorts(
        self,
        *,
        entity: EntityType,
        sorts: Sequence[
            IncidentSearchSort | AssignmentSearchSort
        ] | None,
    ) -> list[IncidentSearchSort | AssignmentSearchSort]:
        if not sorts:
            return []

        normalized = list(sorts)

        if entity == "incidents":
            if not all(
                isinstance(sort, IncidentSearchSort)
                for sort in normalized
            ):
                raise TypeError(
                    "Incident search requires IncidentSearch SearchSort"
                )
            return normalized

        if not all(
            isinstance(sort, AssignmentSearchSort)
            for sort in normalized
        ):
            raise TypeError(
                "Assignment search requires AssignmentSearch SearchSort"
            )

        return normalized