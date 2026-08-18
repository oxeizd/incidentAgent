from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.embeddings.documents import (
    assignment_embedding_text,
    incident_embedding_text,
)
from memory.vectors.repository import VectorRepository


class SupportsEmbeddings(Protocol):
    async def encode(self, texts: Sequence[str]) -> np.ndarray: ...
    async def encode_one(self, text: str) -> np.ndarray: ...


class VectorIndexingService:
    """
    Synchronize semantic vector indexes after domain persistence.

    Embeddings are computed before vector writes. Incidents lacking
    ai_description/reason_inc have no semantic vector: any stale vector is
    deleted instead of storing a zero vector.
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
        text = assignment_embedding_text(assignment)
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
        Batch-index incident artifacts.

        Vector-free incidents still remove stale vectors. This is deliberately
        done even when there are no valid texts in the batch.
        """
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

        vectors = await self._embeddings.encode(
            [text for _, text in with_text]
        )

        if len(vectors) != len(with_text):
            raise RuntimeError(
                "Embedding service returned unexpected vector count: "
                f"expected {len(with_text)}, got {len(vectors)}"
            )

        for (incident, _), vector in zip(with_text, vectors, strict=True):
            await self._vectors.upsert_incident_vector(
                incident_number=incident.number,
                vector=vector,
            )

    async def index_assignments(
        self,
        assignments: Sequence[tuple[str, AssignmentUpsert]],
    ) -> None:
        """
        Batch-index assignment artifacts.

        Each pair consists of the persisted assignment ID and its canonical
        payload. Assignment text is required by the contract.
        """
        if not assignments:
            return

        texts = [
            assignment_embedding_text(assignment)
            for _, assignment in assignments
        ]
        vectors = await self._embeddings.encode(texts)

        if len(vectors) != len(assignments):
            raise RuntimeError(
                "Embedding service returned unexpected vector count: "
                f"expected {len(assignments)}, got {len(vectors)}"
            )

        for (assignment_id, _), vector in zip(
            assignments,
            vectors,
            strict=True,
        ):
            await self._vectors.upsert_assignment_vector(
                assignment_id=assignment_id,
                vector=vector,
            )