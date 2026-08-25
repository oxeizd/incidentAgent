from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

import numpy as np

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.vectors.embeddings.documents import (
    assignment_embedding_text,
    incident_embedding_text,
)
from app.memory.vectors.repository import VectorRepository


T = TypeVar("T")


class SupportsEmbeddings(Protocol):
    async def encode(self, texts: Sequence[str]) -> np.ndarray: ...

    async def encode_one(self, text: str) -> np.ndarray: ...


class VectorIndexingService:
    """
    Синхронизирует semantic indexes после изменения domain artifacts.

    Incident без ai_description/reason_inc не должен иметь вектор:
    stale embedding удаляется. Assignment содержит обязательный текст
    по контракту и всегда индексируется.
    """

    def __init__(
        self,
        *,
        embeddings: SupportsEmbeddings,
        vectors: VectorRepository,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors

    async def index_incident(
        self,
        incident: IncidentUpsert,
    ) -> None:
        text = incident_embedding_text(incident)

        if text is None:
            await self._vectors.delete_incident_vector(
                incident_number=incident.number,
            )
            return

        vector = await self._embeddings.encode_one(text)

        await self._vectors.upsert_incident_vector(
            incident_number=incident.number,
            vector=vector,
        )

    async def index_assignment(
        self,
        assignment: AssignmentUpsert,
        *,
        assignment_id: str,
    ) -> None:
        text = _require_embedding_text(
            assignment_embedding_text(assignment),
            entity_label="Assignment",
        )
        vector = await self._embeddings.encode_one(text)

        await self._vectors.upsert_assignment_vector(
            assignment_id=assignment_id,
            vector=vector,
        )

    async def index_incidents(
        self,
        incidents: Sequence[IncidentUpsert],
    ) -> None:
        """
        Batch index incidents.

        Для incident без embedding text stale vector удаляется даже если
        в текущем batch нет ни одного incident, требующего upsert.
        """
        if not incidents:
            return

        with_text: list[tuple[IncidentUpsert, str]] = []
        without_text: list[IncidentUpsert] = []

        for incident in incidents:
            text = incident_embedding_text(incident)

            if text is None:
                without_text.append(incident)
            else:
                with_text.append((incident, text))

        for incident in without_text:
            await self._vectors.delete_incident_vector(
                incident_number=incident.number,
            )

        if not with_text:
            return

        vectors = await self._encode_batch(
            texts=[text for _, text in with_text],
        )

        for (incident, _), vector in zip(
            with_text,
            vectors,
            strict=True,
        ):
            await self._vectors.upsert_incident_vector(
                incident_number=incident.number,
                vector=vector,
            )

    async def index_assignments(
        self,
        assignments: Sequence[tuple[str, AssignmentUpsert]],
    ) -> None:
        """
        Batch index assignments.

        Каждый элемент — пара persisted assignment ID и canonical payload.
        """
        if not assignments:
            return

        texts = [
            _require_embedding_text(
                assignment_embedding_text(assignment),
                entity_label="Assignment",
            )
            for _, assignment in assignments
        ]
        vectors = await self._encode_batch(texts=texts)

        for (assignment_id, _), vector in zip(
            assignments,
            vectors,
            strict=True,
        ):
            await self._vectors.upsert_assignment_vector(
                assignment_id=assignment_id,
                vector=vector,
            )

    async def _encode_batch(
        self,
        *,
        texts: Sequence[str],
    ) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        vectors = await self._embeddings.encode(texts)

        if len(vectors) != len(texts):
            raise RuntimeError(
                "Embedding service returned unexpected vector count: "
                f"expected {len(texts)}, got {len(vectors)}"
            )

        return vectors


def _require_embedding_text(
    value: str | None,
    *,
    entity_label: str,
) -> str:
    if value is None or not value.strip():
        raise ValueError(
            f"{entity_label} does not contain embedding text"
        )

    return value