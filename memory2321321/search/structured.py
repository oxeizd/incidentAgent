from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.memory.db.connection import Database
from app.memory.search.contracts import SearchExecution, SearchHit
from app.memory.search.queries import (
    AssignmentSearchQuery,
    AssignmentSortField,
    IncidentSearchQuery,
    IncidentSortField,
    SortOrder,
)
from app.memory.search.streaming import validate_batch_size


MAX_ALLOWED_IDS = 500

EntityName = Literal["incidents", "assignments"]
StructuredQuery = IncidentSearchQuery | AssignmentSearchQuery
SortField = IncidentSortField | AssignmentSortField


@dataclass(frozen=True, slots=True)
class EntitySearchConfig:
    """
    Описание безопасного structured search для одной entity.

    Все SQL-выражения и имена колонок здесь — статические whitelist
    значения, никогда не приходящие от LLM или HTTP-клиента.
    """

    entity: EntityName
    table_name: str
    id_column: str
    field_filters: tuple[str, ...]
    date_range_filters: tuple[tuple[str, str, str], ...]
    numeric_range_filters: tuple[str, ...]
    sort_expressions: dict[str, str]
    default_order_by: str
    tie_breaker: str


class SearchSort(BaseModel):
    """
    Один критерий SQL-сортировки.

    Поле ограничено доменным Literal, а SQL выражение выбирается только
    через EntitySearchConfig.sort_expressions.
    """

    model_config = ConfigDict(extra="forbid")

    field: SortField
    order: SortOrder = "desc"


_PRIORITY_RANK_SQL = """
CASE UPPER(COALESCE(priority_code, ''))
    WHEN 'CRITICAL' THEN 4
    WHEN 'HIGH' THEN 3
    WHEN 'MEDIUM' THEN 2
    WHEN 'LOW' THEN 1
    ELSE 0
END
"""


INCIDENT_SEARCH_CONFIG = EntitySearchConfig(
    entity="incidents",
    table_name="incidents",
    id_column="number",
    field_filters=(
        "number",
        "status",
        "priority_code",
        "system_name",
        "work_group",
        "element_name",
        "executor_name",
        "stand",
    ),
    date_range_filters=(
        ("start_time", "start_time_from", "start_time_to"),
        ("end_time", "end_time_from", "end_time_to"),
    ),
    numeric_range_filters=(
        "mttd",
        "mttr",
        "downtime",
    ),
    sort_expressions={
        "start_time": "start_time",
        "end_time": "end_time",
        "mttd": "mttd",
        "mttr": "mttr",
        "downtime": "downtime",
        "priority_code": _PRIORITY_RANK_SQL,
    },
    default_order_by="start_time DESC, number ASC",
    tie_breaker="number ASC",
)

ASSIGNMENT_SEARCH_CONFIG = EntitySearchConfig(
    entity="assignments",
    table_name="assignments",
    id_column="id",
    field_filters=(
        "id",
        "incident_id",
        "ior",
        "unit",
        "responsible",
        "status",
    ),
    date_range_filters=(
        ("deadline", "deadline_from", "deadline_to"),
        ("assigned_at", "assigned_at_from", "assigned_at_to"),
    ),
    numeric_range_filters=(),
    sort_expressions={
        "deadline": "COALESCE(deadline, '9999-12-31')",
        "assigned_at": "assigned_at",
    },
    default_order_by="COALESCE(deadline, '9999-12-31') ASC, id ASC",
    tie_breaker="id ASC",
)


class StructuredSearchService:
    """
    Generic structured SQL search с deterministic ordering.

    Поддерживает:
    - typed query filters/ranges;
    - allowed_ids для semantic candidate set;
    - whitelisted multi-column sort;
    - top_n;
    - streaming batches.
    """

    def __init__(
        self,
        database: Database,
        *,
        config: EntitySearchConfig,
    ) -> None:
        self._database = database
        self._config = config

    async def execute(
        self,
        query: StructuredQuery,
        *,
        sorts: Sequence[SearchSort] | None = None,
        top_n: int | None = None,
        allowed_ids: Sequence[str] | None = None,
    ) -> SearchExecution:
        hits: list[SearchHit] = []

        async for batch in self.stream(
            query,
            batch_size=500,
            sorts=sorts,
            top_n=top_n,
            allowed_ids=allowed_ids,
        ):
            hits.extend(batch)

        normalized_query = query.to_normalized_dict()

        if sorts:
            normalized_query["sort"] = [
                sort.model_dump(mode="json")
                for sort in sorts
            ]

        if top_n is not None:
            normalized_query["top_n"] = top_n

        if allowed_ids is not None:
            normalized_query["semantic_candidate_count"] = len(
                _normalize_allowed_ids(allowed_ids)
            )

        return SearchExecution(
            entity=self._config.entity,
            normalized_query=normalized_query,
            hits=hits,
        )

    async def stream(
        self,
        query: StructuredQuery,
        *,
        batch_size: int,
        sorts: Sequence[SearchSort] | None = None,
        top_n: int | None = None,
        allowed_ids: Sequence[str] | None = None,
    ) -> AsyncIterator[list[SearchHit]]:
        batch_size = validate_batch_size(batch_size)
        normalized_ids = _normalize_allowed_ids(allowed_ids)

        if allowed_ids is not None and not normalized_ids:
            return

        where_sql, params = _build_where(
            query=query,
            config=self._config,
            allowed_ids=normalized_ids,
        )
        order_by_sql = _build_order_by(
            sorts=sorts,
            config=self._config,
        )

        limit_sql = ""
        if top_n is not None:
            if top_n < 1:
                raise ValueError("top_n must be a positive integer")

            limit_sql = " LIMIT ?"
            params.append(top_n)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            (
                f"SELECT * FROM {self._config.table_name}"
                f"{where_sql}"
                f" ORDER BY {order_by_sql}"
                f"{limit_sql}"
            ),
            params,
        )

        while True:
            rows = await cursor.fetchmany(batch_size)
            if not rows:
                break

            yield [
                SearchHit(
                    entity_id=str(row[self._config.id_column]),
                    payload=dict(row),
                )
                for row in rows
            ]


def _normalize_allowed_ids(
    allowed_ids: Sequence[str] | None,
) -> list[str]:
    if allowed_ids is None:
        return []

    unique_ids = list(
        dict.fromkeys(
            value.strip()
            for value in allowed_ids
            if isinstance(value, str) and value.strip()
        )
    )

    if len(unique_ids) > MAX_ALLOWED_IDS:
        raise ValueError(
            "Too many semantic candidate IDs: "
            f"{len(unique_ids)} > {MAX_ALLOWED_IDS}"
        )

    return unique_ids


def _build_where(
    *,
    query: StructuredQuery,
    config: EntitySearchConfig,
    allowed_ids: Sequence[str],
) -> tuple[str, list[Any]]:
    sql = " WHERE 1=1"
    params: list[Any] = []

    for field_name in config.field_filters:
        value = getattr(query, field_name)

        if value is not None:
            sql += f" AND {field_name} = ?"
            params.append(value)

    for column, from_field, to_field in config.date_range_filters:
        value_from = getattr(query, from_field)
        if value_from is not None:
            sql += f" AND {column} >= ?"
            params.append(value_from.isoformat())

        value_to = getattr(query, to_field)
        if value_to is not None:
            sql += f" AND {column} < ?"
            params.append((value_to + timedelta(days=1)).isoformat())

    for field_name in config.numeric_range_filters:
        minimum = getattr(query, f"{field_name}_min")
        if minimum is not None:
            sql += f" AND {field_name} >= ?"
            params.append(minimum)

        maximum = getattr(query, f"{field_name}_max")
        if maximum is not None:
            sql += f" AND {field_name} <= ?"
            params.append(maximum)

    if allowed_ids:
        placeholders = ", ".join("?" for _ in allowed_ids)
        sql += f" AND {config.id_column} IN ({placeholders})"
        params.extend(allowed_ids)

    return sql, params


def _build_order_by(
    *,
    sorts: Sequence[SearchSort] | None,
    config: EntitySearchConfig,
) -> str:
    if not sorts:
        return config.default_order_by

    parts: list[str] = []

    for sort in sorts:
        expression = config.sort_expressions[str(sort.field)]
        direction = "ASC" if sort.order == "asc" else "DESC"
        parts.append(f"{expression} {direction}")

    parts.append(config.tie_breaker)
    return ", ".join(parts)