from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.db.connection import Database
from memory.vectors.indexing import VectorIndexingService


DEFAULT_BACKFILL_BATCH_SIZE = 100
MAX_BACKFILL_BATCH_SIZE = 1_000


class VectorBackfillService:
    """
    Rebuild or complete vector indexes from persisted domain artifacts.

    Semantic content policy stays centralized in VectorIndexingService:
    - incident: ai_description -> reason_inc -> no vector
    - assignment: assignment only

    Processing is cursor-based by hidden SQLite rowid, so a large table is
    never materialized as one Python list.
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
        size = _normalize_batch_size(batch_size)
        after_rowid = 0
        indexed_count = 0

        while True:
            rows = await self._fetch_incident_batch(
                after_rowid=after_rowid,
                batch_size=size,
            )
            if not rows:
                break

            incidents = [
                _incident_from_row(row)
                for row in rows
            ]

            await self._vector_indexing.index_incidents(incidents)

            indexed_count += len(incidents)
            after_rowid = int(rows[-1]["rowid"])

        return indexed_count

    async def backfill_assignments(
        self,
        *,
        batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    ) -> int:
        size = _normalize_batch_size(batch_size)
        after_rowid = 0
        indexed_count = 0

        while True:
            rows = await self._fetch_assignment_batch(
                after_rowid=after_rowid,
                batch_size=size,
            )
            if not rows:
                break

            assignments = [
                (
                    str(row["id"]),
                    _assignment_from_row(row),
                )
                for row in rows
            ]

            await self._vector_indexing.index_assignments(assignments)

            indexed_count += len(assignments)
            after_rowid = int(rows[-1]["rowid"])

        return indexed_count

    async def _fetch_incident_batch(
        self,
        *,
        after_rowid: int,
        batch_size: int,
    ) -> list[Any]:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT rowid, *
            FROM incidents
            WHERE rowid > ?
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (after_rowid, batch_size),
        )

        return list(await cursor.fetchall())

    async def _fetch_assignment_batch(
        self,
        *,
        after_rowid: int,
        batch_size: int,
    ) -> list[Any]:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT rowid, *
            FROM assignments
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
        reason_inc=_as_optional_text(row["reason_inc"]),
    )


def _assignment_from_row(row: Any) -> AssignmentUpsert:
    return AssignmentUpsert(
        id=str(row["id"]),
        assignment=str(row["assignment"]),
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