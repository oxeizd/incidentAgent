from __future__ import annotations

from pathlib import Path

import pytest

from memory.application import MemoryApplication
from memory.search.contracts import DisplaySchema, SearchResultItemRef
from memory.settings import MemorySettings


@pytest.mark.asyncio
async def test_building_search_result_is_hidden_until_marked_ready(
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
        display = DisplaySchema.model_validate(
            {
                "profile": "assignments.table.v1",
                "title": "Поручения",
                "columns": [{"key": "id", "label": "ID"}],
            }
        )

        result = await app.search_results.create_building(
            owner_user_id="user-1",
            source_thread_id=None,
            entity="assignments",
            query={"ior": "ИОР-17"},
            display=display,
        )

        invisible = await app.search_results.get(
            result_id=result.id,
            owner_user_id="user-1",
        )
        assert invisible is None

        await app.search_results.append_items(
            result_id=result.id,
            items=[
                SearchResultItemRef(
                    position=0,
                    entity_id="assignment-1",
                    score=0.42,
                ),
                SearchResultItemRef(
                    position=1,
                    entity_id="assignment-2",
                    score=0.71,
                ),
            ],
        )

        await app.search_results.mark_ready(result_id=result.id)

        visible = await app.search_results.get(
            result_id=result.id,
            owner_user_id="user-1",
        )

        assert visible is not None
        assert visible.status == "ready"
        assert visible.total_count == 2
    finally:
        await app.stop()