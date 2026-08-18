from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_incident_import_reports_bad_items_without_stopping(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]

    app = MemoryApplication(
        MemorySettings(
            database_path=tmp_path / "memory.sqlite3",
            schema_path= project_root / "app" / "memory" / "db" / "schema.sql",
            cleanup_interval_seconds=86_400,
        )
    )
    await app.start()

    try:
        report = await app.imports.import_data(
            entity="incidents",
            raw={
                "incidents": [
                    {
                        "business_id": "INC-1001",
                        "state_code_str": "В работе",
                        "resolution_description": (
                            "Причина инцидента: Ошибка конфигурации."
                        ),
                    },
                    {
                        "state_code_str": "Без номера",
                    },
                    {
                        "business_id": "INC-1003",
                        "mttd": "не число",
                    },
                ]
            },
        )

        assert report.total_items == 3
        assert report.imported_count == 1
        assert report.failed_count == 2
        assert len(report.errors) == 2
        assert report.success is False

        incident = await app.incidents.get("INC-1001")
        assert incident is not None
        assert incident["reason_inc"] == "Ошибка конфигурации."
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_assignment_map_import_adds_soft_incident_reference(
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
        report = await app.imports.import_data(
            entity="assignments",
            raw={
                "INC-1001": [
                    {
                        "assignment_id": "TASK-1",
                        "ior": "ИОР-17",
                        "text": "Подготовить план.",
                    },
                ],
                "INC-1002": [
                    {
                        "assignment_id": "TASK-2",
                        "ior": "ИОР-18",
                        "text": "Согласовать окно работ.",
                    },
                ],
            },
        )

        assert report.success is True
        assert report.total_items == 2
        assert report.imported_count == 2
        assert report.failed_count == 0

        task_one = await app.assignments.get("TASK-1")
        task_two = await app.assignments.get("TASK-2")

        assert task_one is not None
        assert task_two is not None
        assert task_one["incident_id"] == "INC-1001"
        assert task_two["incident_id"] == "INC-1002"
    finally:
        await app.stop()