from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from memory.db.connection import Database
from memory.search.contracts import SearchExecution, SearchHit
from memory.search.queries import IncidentSearchQuery
from memory.search.streaming import validate_batch_size


_FIELD_FILTERS = (
    "number",
    "status",
    "priority_code",
    "system_name",
    "work_group",
    "element_name",
    "executor_name",
    "stand",
)

_DATE_RANGE_FILTERS = (
    ("start_time", "start_time_from", "start_time_to"),
    ("end_time", "end_time_from", "end_time_to"),
)

_NUMERIC_RANGE_FILTERS = ("mttd", "mttr", "downtime")


class IncidentSearch:
    """Structured incident search with deterministic ordering."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def execute(self, query: IncidentSearchQuery) -> SearchExecution:
        hits: list[SearchHit] = []

        async for batch in self.stream(query, batch_size=500):
            hits.extend(batch)

        return SearchExecution(
            entity="incidents",
            normalized_query=query.to_normalized_dict(),
            hits=hits,
        )

    async def stream(
        self,
        query: IncidentSearchQuery,
        *,
        batch_size: int,
    ) -> AsyncIterator[list[SearchHit]]:
        batch_size = validate_batch_size(batch_size)
        where_sql, params = _build_where(query)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            (
                "SELECT * FROM incidents"
                f"{where_sql}"
                " ORDER BY start_time DESC, number ASC"
            ),
            params,
        )

        while True:
            rows = await cursor.fetchmany(batch_size)
            if not rows:
                break

            yield [
                SearchHit(
                    entity_id=str(row["number"]),
                    payload=dict(row),
                )
                for row in rows
            ]


def _build_where(query: IncidentSearchQuery) -> tuple[str, list[Any]]:
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

    for field_name in _NUMERIC_RANGE_FILTERS:
        minimum = getattr(query, f"{field_name}_min")
        if minimum is not None:
            sql += f" AND {field_name} >= ?"
            params.append(minimum)

        maximum = getattr(query, f"{field_name}_max")
        if maximum is not None:
            sql += f" AND {field_name} <= ?"
            params.append(maximum)

    return sql, params