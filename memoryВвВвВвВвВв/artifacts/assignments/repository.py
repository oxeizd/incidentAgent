from __future__ import annotations

import json
import uuid
from typing import Any

from app.memory.artifacts.assignments.contracts import (
    AssignmentUpsert,
)
from app.memory.db.connection import Database
from app.memory.utils import (
    datetime_to_iso,
    normalize_ids,
    placeholders,
    utc_now_iso,
)


_ASSIGNMENT_COLUMNS: tuple[str, ...] = (
    "id",
    "incident_id",
    "ior",
    "task",
    "unit",
    "assignment",
    "responsible",
    "deadline",
    "assigned_at",
    "status",
    "source_payload",
)


class AssignmentRepository:
    """Persistence for independent assignment artifacts."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert(self, payload: AssignmentUpsert) -> str:
        assignment_id = payload.id or f"assignment-{uuid.uuid4().hex}"
        values = payload.model_dump(mode="python")

        values["id"] = assignment_id
        values["deadline"] = datetime_to_iso(values["deadline"])
        values["assigned_at"] = datetime_to_iso(values["assigned_at"])

        source_payload = values["source_payload"]
        values["source_payload"] = (
            json.dumps(
                source_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if source_payload is not None
            else None
        )

        now = utc_now_iso()
        update_columns = tuple(
            column
            for column in _ASSIGNMENT_COLUMNS
            if column != "id"
        )

        async with self._database.transaction() as connection:
            await connection.execute(
                f"""
                INSERT INTO assignments (
                    {", ".join(_ASSIGNMENT_COLUMNS)},
                    created_at,
                    updated_at
                )
                VALUES (
                    {", ".join("?" for _ in _ASSIGNMENT_COLUMNS)},
                    ?,
                    ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    {", ".join(
                        f"{column} = excluded.{column}"
                        for column in update_columns
                    )},
                    updated_at = excluded.updated_at
                """,
                (
                    *(
                        values[column]
                        for column in _ASSIGNMENT_COLUMNS
                    ),
                    now,
                    now,
                ),
            )

        return assignment_id

    async def get(
        self,
        assignment_id: str,
    ) -> dict[str, Any] | None:
        normalized = _require_id(
            assignment_id,
            field_name="assignment_id",
        )
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM assignments
            WHERE id = ?
            """,
            (normalized,),
        )
        row = await cursor.fetchone()

        return dict(row) if row is not None else None

    async def get_many(
        self,
        assignment_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        normalized_ids = normalize_ids(assignment_ids)

        if not normalized_ids:
            return {}

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT *
            FROM assignments
            WHERE id IN ({placeholders(normalized_ids)})
            """,
            normalized_ids,
        )
        rows = await cursor.fetchall()

        return {
            str(row["id"]): dict(row)
            for row in rows
        }


def _require_id(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")

    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field_name} must not be blank")

    return normalized