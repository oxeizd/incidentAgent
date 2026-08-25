from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.db.connection import Database
from app.memory.vectors.indexing import VectorIndexingService


DEFAULT_BACKFILL_BATCH_SIZE = 100
MAX_BACKFILL_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class BackfillEntityConfig:
    """
    Статическая конфигурация cursor-based vector backfill для entity.

    SQL контролируется backend-кодом: никакие имена таблиц или колонок
    не строятся из пользовательского ввода.
    """

    table_name: str
    select_columns: str


_INCIDENT_CONFIG = BackfillEntityConfig(
    table_name="incidents",
    select_columns="""
        rowid,
        number,
        ai_description,
        description,
        reason_inc,
        solution,
        resolution_description,
        impact
    """,
)

_ASSIGNMENT_CONFIG = BackfillEntityConfig(
    table_name="assignments",
    select_columns="""
        rowid,
        id,
        assignment,
        task,
        unit
    """,
)


class VectorBackfillService:
    """
    Rebuilds or completes vector indexes from persisted artifacts.

    Чтение идёт cursor-based по SQLite rowid. Вся таблица не загружается
    в память. Формирование embedding text остаётся в indexing/documents
    слое, этот сервис лишь формирует typed domain payloads из БД.
    """

    def __init__(
        self,
        *,
        database: Database,
        vector_indexing: VectorIndexingService,
    ) -> None:
        self._database = database
        self._vector_indexing = vector_indexing

    async def backfill_incidents(
        self,
        *,
        batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    ) -> int:
        return await self._backfill(
            config=_INCIDENT_CONFIG,
            batch_size=batch_size,
            row_to_item=_incident_from_row,
            index_batch=self._vector_indexing.index_incidents,
        )

    async def backfill_assignments(
        self,
        *,
        batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    ) -> int:
        return await self._backfill(
            config=_ASSIGNMENT_CONFIG,
            batch_size=batch_size,
            row_to_item=_assignment_from_row,
            index_batch=self._vector_indexing.index_assignments,
        )

    async def _backfill(
        self,
        *,
        config: BackfillEntityConfig,
        batch_size: int,
        row_to_item: Callable[[Any], Any],
        index_batch: Callable[[Sequence[Any]], Awaitable[None]],
    ) -> int:
        size = _normalize_batch_size(batch_size)
        after_rowid = 0
        indexed_count = 0

        while True:
            rows = await self._fetch_batch(
                config=config,
                after_rowid=after_rowid,
                batch_size=size,
            )
            if not rows:
                break

            items = [row_to_item(row) for row in rows]
            await index_batch(items)

            indexed_count += len(items)
            after_rowid = int(rows[-1]["rowid"])

        return indexed_count

    async def _fetch_batch(
        self,
        *,
        config: BackfillEntityConfig,
        after_rowid: int,
        batch_size: int,
    ) -> list[Any]:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            f"""
            SELECT {config.select_columns}
            FROM {config.table_name}
            WHERE rowid > ?
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (after_rowid, batch_size),
        )

        return list(await cursor.fetchall())


def _incident_from_row(row: Any) -> IncidentUpsert:
    return IncidentUpsert(
        number=str(row["number"]),
        ai_description=_as_optional_text(row["ai_description"]),
        description=_as_optional_text(row["description"]),
        reason_inc=_as_optional_text(row["reason_inc"]),
        solution=_as_optional_text(row["solution"]),
        resolution_description=_as_optional_text(
            row["resolution_description"],
        ),
        impact=_as_optional_text(row["impact"]),
    )


def _assignment_from_row(
    row: Any,
) -> tuple[str, AssignmentUpsert]:
    assignment_id = str(row["id"])

    return (
        assignment_id,
        AssignmentUpsert(
            id=assignment_id,
            assignment=str(row["assignment"]),
            task=_as_optional_text(row["task"]),
            unit=_as_optional_text(row["unit"]),
        ),
    )


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _normalize_batch_size(value: int) -> int:
    if value < 1:
        raise ValueError("batch_size must be at least 1")

    return min(value, MAX_BACKFILL_BATCH_SIZE)