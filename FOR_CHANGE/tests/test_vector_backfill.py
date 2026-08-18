from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.embeddings.testing import DeterministicEmbeddingProvider
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_backfill_indexes_only_semantically_eligible_records(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]

    settings = MemorySettings(
        database_path=tmp_path / "memory.sqlite3",
        schema_path=root / "memory" / "db" / "schema.sql",
        embedding_model_path="test-model-not-loaded",
        vector_dimension=3,
        cleanup_interval_seconds=86_400,
    )

    app = MemoryApplication(
        settings,
        embedding_provider=DeterministicEmbeddingProvider(
            dimension=settings.vector_dimension,
        ),
    )
    await app.start()

    try:
        await app.incidents.upsert(
            IncidentUpsert(
                number="INC-1",
                reason_inc="Ошибка маршрутизации запросов.",
            )
        )
        await app.incidents.upsert(
            IncidentUpsert(
                number="INC-2",
                ai_description="Сбой после неверной конфигурации.",
                reason_inc="Старое ручное описание.",
            )
        )
        await app.incidents.upsert(
            IncidentUpsert(
                number="INC-3",
                system_name="Billing",
            )
        )

        await app.assignments.upsert(
            AssignmentUpsert(
                id="TASK-1",
                assignment="Подготовить план восстановления.",
            )
        )
        await app.assignments.upsert(
            AssignmentUpsert(
                id="TASK-2",
                assignment="Проверить конфигурацию маршрутизации.",
            )
        )

        incident_processed = await app.vector_backfill.backfill_incidents(
            batch_size=1,
        )
        assignment_processed = await app.vector_backfill.backfill_assignments(
            batch_size=1,
        )

        assert incident_processed == 3
        assert assignment_processed == 2

        connection = await app._database.read_connection()

        incident_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM incident_vectors"
        )
        assignment_cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM assignment_vectors"
        )

        incident_count = (await incident_cursor.fetchone())["count"]
        assignment_count = (await assignment_cursor.fetchone())["count"]

        assert incident_count == 2
        assert assignment_count == 2
    finally:
        await app.stop()