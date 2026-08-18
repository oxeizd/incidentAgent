from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.db.connection import Database


ASSIGNMENT_COLUMNS = (
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.isoformat()

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
        values["source_payload"] = (
            json.dumps(
                values["source_payload"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if values["source_payload"] is not None
            else None
        )

        now = utc_now_iso()
        update_columns = tuple(
            column
            for column in ASSIGNMENT_COLUMNS
            if column != "id"
        )

        async with self._database.transaction() as connection:
            await connection.execute(
                f"""
                INSERT INTO assignments (
                    {", ".join((*ASSIGNMENT_COLUMNS, "created_at", "updated_at"))}
                )
                VALUES (
                    {", ".join(
                        "?"
                        for _ in (*ASSIGNMENT_COLUMNS, "created_at", "updated_at")
                    )}
                )
                ON CONFLICT(id) DO UPDATE SET
                    {", ".join(
                        f"{column} = excluded.{column}"
                        for column in update_columns
                    )},
                    updated_at = excluded.updated_at
                """,
                [
                    *(values[column] for column in ASSIGNMENT_COLUMNS),
                    now,
                    now,
                ],
            )

        return assignment_id

    async def get(self, assignment_id: str) -> dict[str, Any] | None:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            "SELECT * FROM assignments WHERE id = ?",
            (assignment_id,),
        )
        row = await cursor.fetchone()

        return dict(row) if row is not None else None

    async def get_many(
        self,
        assignment_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not assignment_ids:
            return {}

        connection = await self._database.read_connection()
        placeholders = ",".join("?" for _ in assignment_ids)

        cursor = await connection.execute(
            f"SELECT * FROM assignments WHERE id IN ({placeholders})",
            assignment_ids,
        )
        rows = await cursor.fetchall()

        return {
            str(row["id"]): dict(row)
            for row in rows
        }