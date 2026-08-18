from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from memory.application import MemoryApplication
from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_vector_repository_upserts_and_deletes_domain_vectors(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]

    app = MemoryApplication(
        MemorySettings(
            database_path=tmp_path / "memory.sqlite3",
            schema_path=root / "app" / "memory" / "db" / "schema.sql",
            embedding_model_path="test-model-not-loaded",
            vector_dimension=3,
            cleanup_interval_seconds=86_400,
        )
    )
    await app.start()

    try:
        await app.incidents.upsert(
            IncidentUpsert(
                number="INC-1001",
                reason_inc="Ошибка маршрутизации.",
            )
        )
        await app.assignments.upsert(
            AssignmentUpsert(
                id="TASK-1",
                assignment="Подготовить план восстановления.",
            )
        )

        await app.vectors.upsert_incident_vector(
            incident_number="INC-1001",
            vector=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )
        await app.vectors.upsert_assignment_vector(
            assignment_id="TASK-1",
            vector=np.array([0.4, 0.5, 0.6], dtype=np.float32),
        )

        connection = await app._database.read_connection()

        incident_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM incident_vectors"
        )
        assignment_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM assignment_vectors"
        )

        incident_row = await incident_cursor.fetchone()
        assignment_row = await assignment_cursor.fetchone()

        assert incident_row["count"] == 1
        assert assignment_row["count"] == 1

        await app.vectors.delete_incident_vector(incident_number="INC-1001")
        await app.vectors.delete_assignment_vector(assignment_id="TASK-1")

        incident_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM incident_vectors"
        )
        assignment_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM assignment_vectors"
        )

        incident_row = await incident_cursor.fetchone()
        assignment_row = await assignment_cursor.fetchone()

        assert incident_row["count"] == 0
        assert assignment_row["count"] == 0
    finally:
        await app.stop()