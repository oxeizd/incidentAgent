from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.search.queries import AssignmentSearchQuery, IncidentSearchQuery
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_search_service_rejects_query_for_another_entity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]

    app = MemoryApplication(
        MemorySettings(
            database_path=tmp_path / "memory.sqlite3",
            schema_path=root / "app" / "memory" / "db" / "schema.sql",
            cleanup_interval_seconds=86_400,
        )
    )
    await app.start()

    try:
        thread_id = await app.threads.create_thread(user_id="user-1")

        with pytest.raises(TypeError, match="Incident search requires"):
            await app.search.search_and_post(
                entity="incidents",
                query=AssignmentSearchQuery(ior="ИОР-17"),
                owner_user_id="user-1",
                thread_id=thread_id,
            )

        with pytest.raises(TypeError, match="Assignment search requires"):
            await app.search.search_and_post(
                entity="assignments",
                query=IncidentSearchQuery(status="В работе"),
                owner_user_id="user-1",
                thread_id=thread_id,
            )
    finally:
        await app.stop()