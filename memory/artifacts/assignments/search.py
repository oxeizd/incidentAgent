from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from memory.db.connection import Database
from memory.search.contracts import SearchExecution, SearchHit
from memory.search.queries import AssignmentSearchQuery
from memory.search.streaming import validate_batch_size


_FIELD_FILTERS = (
    "id",
    "incident_id",
    "ior",
    "unit",
    "responsible",
    "status",
)

_DATE_RANGE_FILTERS = (
    ("deadline", "deadline_from", "deadline_to"),
    ("assigned_at", "assigned_at_from", "assigned_at_to"),
)


class AssignmentSearch:
    """Structured assignment search with deterministic ordering."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def execute(self, query: AssignmentSearchQuery) -> SearchExecution:
        hits: list[SearchHit] = []

        async for batch in self.stream(query, batch_size=500):
            hits.extend(batch)

        return SearchExecution(
            entity="assignments",
            normalized_query=query.to_normalized_dict(),
            hits=hits,
        )

    async def stream(
        self,
        query: AssignmentSearchQuery,
        *,
        batch_size: int,
    ) -> AsyncIterator[list[SearchHit]]:
        batch_size = validate_batch_size(batch_size)
        where_sql, params = _build_where(query)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            (
                "SELECT * FROM assignments"
                f"{where_sql}"
                " ORDER BY COALESCE(deadline, '9999-12-31') ASC, id ASC"
            ),
            params,
        )

        while True:
            rows = await cursor.fetchmany(batch_size)
            if not rows:
                break

            yield [
                SearchHit(
                    entity_id=str(row["id"]),
                    payload=dict(row),
                )
                for row in rows
            ]


def _build_where(query: AssignmentSearchQuery) -> tuple[str, list[Any]]:
    sql = " WHERE 1=1"
    params: list[Any] = []

    for field_name in _FIELD_FILTERS:
        value = getattr(query, field_name)

        if value is not None:
            sql += f" AND {field_name} = ?"
            params.append(value)

    for column, from_field, to_field in _DATE_RANGE_FILTERS:
        value_from = getattr(query, from_field)
        if value_from is not None:
            sql += f" AND {column} >= ?"
            params.append(value_from.isoformat())

        value_to = getattr(query, to_field)
        if value_to is not None:
            sql += f" AND {column} < ?"
            params.append((value_to + timedelta(days=1)).isoformat())

    return sql, params