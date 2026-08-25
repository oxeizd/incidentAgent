from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

from app.memory.artifacts.assignments.repository import (
    AssignmentRepository,
)
from app.memory.artifacts.incidents.repository import (
    IncidentRepository,
)
from app.memory.search.contracts import SearchExecution, SearchHit
from app.memory.vectors.repository import VectorRepository


MIN_SIMILARITY = 0.5

EntityType = Literal["incidents", "assignments"]
VectorFetcher = Callable[..., Awaitable[dict[str, np.ndarray]]]
PayloadFetcher = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]
KnnFinder = Callable[..., Awaitable[list[tuple[str, float]]]]


class SupportsQueryEmbedding(Protocol):
    async def encode_one(self, text: str) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class SemanticEntityConfig:
    """
    Статическая конфигурация semantic search для доменной entity.

    Содержит только backend-controlled функции и metadata, никогда не
    принимается из API, LLM или пользовательского ввода.
    """

    entity: EntityType
    vector_source: str
    vector_fetcher: VectorFetcher
    vector_id_kwarg: str
    knn_finder: KnnFinder
    payload_fetcher: PayloadFetcher


class SemanticSearchService:
    """
    Semantic nearest-neighbor retrieval for incidents and assignments.

    Один generic engine работает с entity-specific config:
    - allowed_ids=None: глобальный KNN через sqlite-vec;
    - allowed_ids задан: cosine rerank только внутри SQL-filtered IDs.

    Во всех режимах результаты ниже MIN_SIMILARITY не возвращаются.
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
        self._entities: dict[EntityType, SemanticEntityConfig] = {
            "incidents": SemanticEntityConfig(
                entity="incidents",
                vector_source="description_reason_solution",
                vector_fetcher=vectors.get_incident_vectors,
                vector_id_kwarg="incident_numbers",
                knn_finder=vectors.find_similar_incident_ids,
                payload_fetcher=incident_repository.get_many,
            ),
            "assignments": SemanticEntityConfig(
                entity="assignments",
                vector_source="assignment",
                vector_fetcher=vectors.get_assignment_vectors,
                vector_id_kwarg="assignment_ids",
                knn_finder=vectors.find_similar_assignment_ids,
                payload_fetcher=assignment_repository.get_many,
            ),
        }

    async def similar_incidents(
        self,
        *,
        query_text: str,
        limit: int = 100,
        allowed_ids: set[str] | None = None,
    ) -> SearchExecution:
        return await self._search(
            entity="incidents",
            query_text=query_text,
            limit=limit,
            allowed_ids=allowed_ids,
        )

    async def similar_assignments(
        self,
        *,
        query_text: str,
        limit: int = 100,
        allowed_ids: set[str] | None = None,
    ) -> SearchExecution:
        return await self._search(
            entity="assignments",
            query_text=query_text,
            limit=limit,
            allowed_ids=allowed_ids,
        )

    async def _search(
        self,
        *,
        entity: EntityType,
        query_text: str,
        limit: int,
        allowed_ids: set[str] | None,
    ) -> SearchExecution:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        normalized_query = _require_query_text(query_text)
        query_vector = await self._embeddings.encode_one(
            normalized_query
        )
        config = self._entities[entity]

        matches = await self._find_matches(
            config=config,
            query_vector=query_vector,
            allowed_ids=allowed_ids,
            limit=limit,
        )

        entity_ids = [entity_id for entity_id, _ in matches]
        payloads = await config.payload_fetcher(entity_ids)

        return SearchExecution(
            entity=entity,
            normalized_query={
                "mode": "semantic_similarity",
                "query_text": normalized_query,
                "limit": limit,
                "filtered": allowed_ids is not None,
                "min_similarity": MIN_SIMILARITY,
                "vector_source": config.vector_source,
            },
            hits=[
                SearchHit(
                    entity_id=entity_id,
                    payload=payload,
                    score=similarity,
                )
                for entity_id, similarity in matches
                if (
                    payload := payloads.get(entity_id)
                ) is not None
            ],
        )

    async def _find_matches(
        self,
        *,
        config: SemanticEntityConfig,
        query_vector: np.ndarray,
        allowed_ids: set[str] | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        if allowed_ids is not None:
            return await _rank_within_allowed_ids(
                query_vector=query_vector,
                allowed_ids=allowed_ids,
                limit=limit,
                fetch_vectors=config.vector_fetcher,
                id_kwarg=config.vector_id_kwarg,
            )

        raw_matches = await config.knn_finder(
            query_vector=query_vector,
            limit=limit,
        )

        return _filter_and_rank_knn_matches(
            raw_matches,
            limit=limit,
        )


async def _rank_within_allowed_ids(
    *,
    query_vector: np.ndarray,
    allowed_ids: set[str],
    limit: int,
    fetch_vectors: VectorFetcher,
    id_kwarg: str,
) -> list[tuple[str, float]]:
    if not allowed_ids:
        return []

    vectors_by_id = await fetch_vectors(
        **{id_kwarg: list(allowed_ids)}
    )
    if not vectors_by_id:
        return []

    query = np.asarray(query_vector, dtype=np.float32)
    query_norm = float(np.linalg.norm(query))

    if query_norm == 0.0:
        raise ValueError("Query embedding must not have zero norm")

    normalized_query = query / query_norm
    scored: list[tuple[str, float]] = []

    for entity_id, vector in vectors_by_id.items():
        candidate = np.asarray(vector, dtype=np.float32)
        candidate_norm = float(np.linalg.norm(candidate))

        if candidate_norm == 0.0:
            continue

        similarity = float(
            np.dot(normalized_query, candidate / candidate_norm)
        )

        if similarity >= MIN_SIMILARITY:
            scored.append((entity_id, similarity))

    scored.sort(
        key=lambda item: (-item[1], item[0]),
    )
    return scored[:limit]


def _filter_and_rank_knn_matches(
    raw_matches: list[tuple[str, float]],
    *,
    limit: int,
) -> list[tuple[str, float]]:
    """
    Converts sqlite-vec L2 distance to cosine similarity.

    Предполагает unit-normalized embeddings:
    cosine_similarity = 1 - distance² / 2.
    """
    scored: list[tuple[str, float]] = []

    for entity_id, distance in raw_matches:
        similarity = 1.0 - (float(distance) ** 2) / 2.0

        if similarity >= MIN_SIMILARITY:
            scored.append((entity_id, similarity))

    scored.sort(
        key=lambda item: (-item[1], item[0]),
    )
    return scored[:limit]


def _require_query_text(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("Semantic query text must not be empty")

    return normalized