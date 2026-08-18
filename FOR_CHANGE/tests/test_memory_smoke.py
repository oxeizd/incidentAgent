from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.search_results.api import get_search_result_table_page
from memory.search.queries import AssignmentSearchQuery
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_memory_layer_bootstraps_empty_database_and_search_snapshot(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]

    database_path = tmp_path / "runtime" / "memory.sqlite3"
    schema_path = project_root / "app" / "memory" / "db" / "schema.sql"

    assert not database_path.exists()
    assert schema_path.exists(), f"Schema file was not found: {schema_path}"

    settings = MemorySettings(
        database_path=database_path,
        schema_path=schema_path,
        search_preview_limit=2,
        cleanup_interval_seconds=86_400,
    )
    app = MemoryApplication(settings)

    await app.start()

    try:
        assert database_path.exists()

        thread_id = await app.threads.create_thread(
            user_id="user-1",
            title="Smoke test thread",
        )

        await app.assignments.upsert(
            AssignmentUpsert(
                id="assignment-001",
                incident_id="INC-1001",
                ior="ИОР-17",
                task="Восстановление API Gateway",
                unit="Эксплуатация",
                assignment=(
                    "Подготовить и согласовать план восстановления "
                    "API Gateway."
                ),
                responsible="Иванов И.И.",
                deadline="2026-08-20",
                status="В работе",
            )
        )
        await app.assignments.upsert(
            AssignmentUpsert(
                id="assignment-002",
                incident_id="INC-1002",
                ior="ИОР-17",
                task="Окно технических работ",
                unit="Эксплуатация",
                assignment=(
                    "Согласовать окно технических работ "
                    "с владельцем сервиса."
                ),
                responsible="Петров П.П.",
                deadline="2026-08-21",
                status="Новое",
            )
        )
        await app.assignments.upsert(
            AssignmentUpsert(
                id="assignment-003",
                incident_id="INC-1003",
                ior="ИОР-18",
                task="Проверка мониторинга",
                unit="SRE",
                assignment="Проверить алерты и метрики после восстановления.",
                responsible="Сидоров С.С.",
                deadline="2026-08-22",
                status="В работе",
            )
        )

        message_id = await app.search.search_and_post(
            entity="assignments",
            query=AssignmentSearchQuery(ior="ИОР-17"),
            owner_user_id="user-1",
            thread_id=thread_id,
            preview_limit=1,
        )

        assert message_id.startswith("message-")

        messages = await app.threads.get_messages(thread_id)
        assert len(messages) == 1

        message = messages[0]
        assert message["role"] == "assistant"
        assert "Найдено: **2**" in message["content"]

        artifact = message["artifact"]
        assert artifact is not None
        assert artifact["artifact_type"] == "memory.search_result_ref"
        assert artifact["artifact_version"] == 1
        assert artifact["entity"] == "assignments"
        assert artifact["total_count"] == 2
        assert artifact["preview_count"] == 1
        assert len(artifact["preview"]["rows"]) == 1

        result_id = artifact["result_id"]

        first_page = await get_search_result_table_page(
            search_result_repository=app.search_results,
            incident_repository=app.incidents,
            assignment_repository=app.assignments,
            result_id=result_id,
            owner_user_id="user-1",
            cursor=None,
            limit=1,
        )

        assert first_page["artifact_type"] == "memory.search_result_table_page"
        assert first_page["entity"] == "assignments"
        assert first_page["total_count"] == 2
        assert first_page["page"]["returned_count"] == 1
        assert first_page["page"]["has_more"] is True
        assert first_page["page"]["next_cursor"] is not None

        first_item = first_page["items"][0]
        assert first_item["available"] is True
        assert first_item["deleted"] is False
        assert first_item["payload"]["id"] == "assignment-001"
        assert first_item["payload"]["ior"] == "ИОР-17"

        second_page = await get_search_result_table_page(
            search_result_repository=app.search_results,
            incident_repository=app.incidents,
            assignment_repository=app.assignments,
            result_id=result_id,
            owner_user_id="user-1",
            cursor=first_page["page"]["next_cursor"],
            limit=1,
        )

        assert second_page["page"]["returned_count"] == 1
        assert second_page["page"]["has_more"] is False
        assert second_page["page"]["next_cursor"] is None

        second_item = second_page["items"][0]
        assert second_item["available"] is True
        assert second_item["deleted"] is False
        assert second_item["payload"]["id"] == "assignment-002"

    finally:
        await app.stop()