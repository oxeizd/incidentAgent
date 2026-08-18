from __future__ import annotations

import numpy as np
import pytest

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.vectors.indexing import VectorIndexingService


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def encode_one(self, text: str) -> np.ndarray:
        self.calls.append(text)
        return np.array([0.1, 0.2, 0.3], dtype=np.float32)


class FakeVectors:
    def __init__(self) -> None:
        self.upserted_incidents: list[tuple[str, np.ndarray]] = []
        self.deleted_incidents: list[str] = []
        self.upserted_assignments: list[tuple[str, np.ndarray]] = []

    async def upsert_incident_vector(
        self,
        *,
        incident_number: str,
        vector: np.ndarray,
    ) -> None:
        self.upserted_incidents.append((incident_number, vector))

    async def delete_incident_vector(
        self,
        *,
        incident_number: str,
    ) -> None:
        self.deleted_incidents.append(incident_number)

    async def upsert_assignment_vector(
        self,
        *,
        assignment_id: str,
        vector: np.ndarray,
    ) -> None:
        self.upserted_assignments.append((assignment_id, vector))


@pytest.mark.asyncio
async def test_incident_index_prefers_ai_description() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()

    service = VectorIndexingService(
        embeddings=embeddings,
        vectors=vectors,  # type: ignore[arg-type]
    )

    await service.index_incident(
        IncidentUpsert(
            number="INC-1",
            ai_description="AI summary of the incident reason.",
            reason_inc="Raw manual reason.",
        )
    )

    assert embeddings.calls == ["AI summary of the incident reason."]
    assert len(vectors.upserted_incidents) == 1
    assert vectors.upserted_incidents[0][0] == "INC-1"
    assert vectors.deleted_incidents == []


@pytest.mark.asyncio
async def test_incident_without_semantic_text_removes_stale_vector() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()

    service = VectorIndexingService(
        embeddings=embeddings,
        vectors=vectors,  # type: ignore[arg-type]
    )

    await service.index_incident(
        IncidentUpsert(
            number="INC-2",
            system_name="Billing",
        )
    )

    assert embeddings.calls == []
    assert vectors.upserted_incidents == []
    assert vectors.deleted_incidents == ["INC-2"]


@pytest.mark.asyncio
async def test_assignment_index_uses_only_assignment_text() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()

    service = VectorIndexingService(
        embeddings=embeddings,
        vectors=vectors,  # type: ignore[arg-type]
    )

    await service.index_assignment(
        AssignmentUpsert(
            incident_id="INC-1",
            ior="ИОР-17",
            responsible="Иванов И.И.",
            assignment="Подготовить план восстановления.",
        ),
        assignment_id="TASK-1",
    )

    assert embeddings.calls == ["Подготовить план восстановления."]
    assert len(vectors.upserted_assignments) == 1
    assert vectors.upserted_assignments[0][0] == "TASK-1"