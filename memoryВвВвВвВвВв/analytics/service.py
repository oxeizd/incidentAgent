from __future__ import annotations

import re
from typing import Any

from app.memory.analytics.contracts import (
    AnalyticsSqlRequest,
    AnalyticsSqlResult,
)
from app.memory.db.connection import Database


_FORBIDDEN_KEYWORDS_RE = re.compile(
    r"""
    \b(
        INSERT|UPDATE|DELETE|REPLACE|UPSERT|MERGE|
        CREATE|DROP|ALTER|VACUUM|REINDEX|
        ATTACH|DETACH|PRAGMA|
        BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|
        EXPLAIN|ANALYZE|
        LOAD_EXTENSION
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_RELATION_RE = re.compile(
    r"""
    \b(?:FROM|JOIN)\s+
    (?P<name>[a-z_][a-z0-9_]*)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ALLOWED_RELATIONS = frozenset(
    {
        "analytics_incidents",
        "analytics_assignments",
    }
)


class AnalyticsSqlService:
    """
    Ограниченный read-only SQL tool для аналитических вопросов.

    Это не SQL console. Запрос может читать только заранее определённые
    analytics views и возвращает ограниченное число JSON-safe строк.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def execute(
        self,
        request: AnalyticsSqlRequest,
    ) -> AnalyticsSqlResult:
        sql = _validate_sql(request.sql)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            sql,
            tuple(request.parameters),
        )

        rows = await cursor.fetchmany(request.max_rows + 1)
        truncated = len(rows) > request.max_rows
        visible_rows = rows[: request.max_rows]

        columns = [
            str(column[0])
            for column in cursor.description or []
        ]

        return AnalyticsSqlResult(
            columns=columns,
            rows=[
                {
                    column: _to_json_safe_value(row[column])
                    for column in columns
                }
                for row in visible_rows
            ],
            truncated=truncated,
        )


def _validate_sql(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("SQL query must be a string")

    sql = " ".join(value.strip().split())

    if not sql:
        raise ValueError("SQL query must not be empty")

    if ";" in sql:
        raise ValueError("SQL query must contain exactly one statement")

    upper_sql = sql.upper()

    if not (
        upper_sql.startswith("SELECT ")
        or upper_sql.startswith("WITH ")
    ):
        raise ValueError(
            "Only SELECT or WITH ... SELECT queries are allowed"
        )

    if _FORBIDDEN_KEYWORDS_RE.search(sql):
        raise ValueError(
            "SQL contains a forbidden statement or keyword"
        )

    relations = {
        match.group("name").casefold()
        for match in _RELATION_RE.finditer(sql)
    }

    if not relations:
        raise ValueError(
            "SQL query must read from an analytics view"
        )

    forbidden_relations = relations - _ALLOWED_RELATIONS

    if forbidden_relations:
        names = ", ".join(sorted(forbidden_relations))
        raise ValueError(
            f"SQL reads from forbidden relation(s): {names}"
        )

    return sql


def _to_json_safe_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)