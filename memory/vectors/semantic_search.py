from __future__ import annotations

from typing import Protocol

import numpy as np

from memory.artifacts.assignments.repository import AssignmentRepository
from memory.artifacts.incidents.repository import IncidentRepository
from memory.search.contracts import SearchExecution, SearchHit
from memory.vectors.repository import VectorRepository


class SupportsQueryEmbedding(Protocol):
    async def encode_one(self, text: str) -> np.ndarray: ...


class SemanticSearchService:
    """
    Semantic nearest-neighbor retrieval for similar incidents and assignments.

    This service intentionally does not accept structured SQL filters:
    - incident similarity means reason/AI-description similarity;
    - assignment similarity means assignment-text similarity.

    Exact attributes such as names, IORs, dates, statuses and systems belong
    to the separate structured SQL search flow.
    """

    def __init__(
        self,
        *,
        embeddings: SupportsQueryEmbedding,
        vectors: VectorRepository,
        incident_repository: IncidentRepository,
        assignment_repository: AssignmentRepository,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._incident_repository = incident_repository
        self._assignment_repository = assignment_repository

    async def similar_incidents(
        self,
        *,
        query_text: str,
        limit: int = 100,
    ) -> SearchExecution:
        normalized_query = _require_query_text(query_text)
        query_vector = await self._embeddings.encode_one(normalized_query)

        matches = await self._vectors.find_similar_incident_ids(
            query_vector=query_vector,
            limit=limit,
        )
        entity_ids = [entity_id for entity_id, _ in matches]

        payloads = await self._incident_repository.get_many(entity_ids)

        return SearchExecution(
            entity="incidents",
            normalized_query={
                "mode": "semantic_similarity",
                "query_text": normalized_query,
                "limit": limit,
                "vector_source": "ai_description_or_reason_inc",
            },
            hits=[
                SearchHit(
                    entity_id=entity_id,
                    payload=payload,
                    score=distance,
                )
                for entity_id, distance in matches
                if (payload := payloads.get(entity_id)) is not None
            ],
        )

    async def similar_assignments(
        self,
        *,
        query_text: str,
        limit: int = 100,
    ) -> SearchExecution:
        normalized_query = _require_query_text(query_text)
        query_vector = await self._embeddings.encode_one(normalized_query)

        matches = await self._vectors.find_similar_assignment_ids(
            query_vector=query_vector,
            limit=limit,
        )
        entity_ids = [entity_id for entity_id, _ in matches]

        payloads = await self._assignment_repository.get_many(entity_ids)

        return SearchExecution(
            entity="assignments",
            normalized_query={
                "mode": "semantic_similarity",
                "query_text": normalized_query,
                "limit": limit,
                "vector_source": "assignment",
            },
            hits=[
                SearchHit(
                    entity_id=entity_id,
                    payload=payload,
                    score=distance,
                )
                for entity_id, distance in matches
                if (payload := payloads.get(entity_id)) is not None
            ],
        )


def _require_query_text(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("Semantic query text must not be empty")

    return normalized