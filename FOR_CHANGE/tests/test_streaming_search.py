from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.search_results.api import get_search_result_table_page
from memory.search.queries import AssignmentSearchQuery
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_streaming_search_persists_complete_snapshot_and_small_preview(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]

    app = MemoryApplication(
        MemorySettings(
            database_path=tmp_path / "memory.sqlite3",
            schema_path=project_root / "app" / "memory" / "db" / "schema.sql",
            search_preview_limit=2,
            cleanup_interval_seconds=86_400,
        )
    )

    await app.start()

    try:
        thread_id = await app.threads.create_thread(user_id="user-1")

        for index in range(7):
            await app.assignments.upsert(
                AssignmentUpsert(
                    id=f"assignment-{index:03d}",
                    ior="ИОР-17",
                    assignment=f"Поручение номер {index}",
                    deadline=f"2026-08-{index + 10:02d}",
                )
            )

        message_id = await app.search.search_and_post(
            entity="assignments",
            query=AssignmentSearchQuery(ior="ИОР-17"),
            owner_user_id="user-1",
            thread_id=thread_id,
            batch_size=3,
        )

        assert message_id.startswith("message-")

        messages = await app.threads.get_messages(thread_id)
        artifact = messages[-1]["artifact"]

        assert artifact is not None
        assert artifact["total_count"] == 7
        assert artifact["preview_count"] == 2
        assert len(artifact["preview"]["rows"]) == 2

        result_id = artifact["result_id"]

        first_page = await get_search_result_table_page(
            search_result_repository=app.search_results,
            incident_repository=app.incidents,
            assignment_repository=app.assignments,
            result_id=result_id,
            owner_user_id="user-1",
            cursor=None,
            limit=3,
        )

        assert first_page["page"]["returned_count"] == 3
        assert first_page["page"]["has_more"] is True
        assert first_page["page"]["next_cursor"] is not None

        second_page = await get_search_result_table_page(
            search_result_repository=app.search_results,
            incident_repository=app.incidents,
            assignment_repository=app.assignments,
            result_id=result_id,
            owner_user_id="user-1",
            cursor=first_page["page"]["next_cursor"],
            limit=3,
        )

        assert second_page["page"]["returned_count"] == 3
        assert second_page["page"]["has_more"] is True
        assert second_page["page"]["next_cursor"] is not None

        third_page = await get_search_result_table_page(
            search_result_repository=app.search_results,
            incident_repository=app.incidents,
            assignment_repository=app.assignments,
            result_id=result_id,
            owner_user_id="user-1",
            cursor=second_page["page"]["next_cursor"],
            limit=3,
        )

        assert third_page["page"]["returned_count"] == 1
        assert third_page["page"]["has_more"] is False
        assert third_page["page"]["next_cursor"] is None

        ids = [
            item["payload"]["id"]
            for page in (first_page, second_page, third_page)
            for item in page["items"]
        ]

        assert ids == [
            "assignment-000",
            "assignment-001",
            "assignment-002",
            "assignment-003",
            "assignment-004",
            "assignment-005",
            "assignment-006",
        ]
    finally:
        await app.stop()