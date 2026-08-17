from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.db.connection import Database


INCIDENT_COLUMNS = (
    "number",
    "created_at",
    "target_date",
    "plan_finish_date",
    "close_date",
    "detection_time",
    "work_group",
    "element_name",
    "system_name",
    "created_by",
    "executor_name",
    "status",
    "priority_code",
    "resolution_code",
    "registration_basis",
    "inc_type",
    "stand",
    "description",
    "resolution_description",
    "reason_inc",
    "solution",
    "impact",
    "start_time",
    "end_time",
    "impact_custom_service",
    "no_impact",
    "is_root",
    "mttd",
    "mttr",
    "downtime",
    "month_created",
    "quarter_created",
    "ai_description",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.isoformat()

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class IncidentRepository:
    """Persistence for independent incident artifacts."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert(self, payload: IncidentUpsert) -> str:
        values = payload.model_dump(mode="python")

        for field_name in (
            "created_at",
            "target_date",
            "plan_finish_date",
            "close_date",
            "detection_time",
            "start_time",
            "end_time",
        ):
            values[field_name] = datetime_to_iso(values[field_name])

        for field_name in (
            "impact_custom_service",
            "no_impact",
            "is_root",
        ):
            values[field_name] = int(values[field_name])

        now = utc_now_iso()
        update_columns = tuple(
            column
            for column in INCIDENT_COLUMNS
            if column != "number"
        )

        async with self._database.transaction() as connection:
            await connection.execute(
                f"""
                INSERT INTO incidents (
                    {", ".join((*INCIDENT_COLUMNS, "updated_at"))}
                )
                VALUES (
                    {", ".join("?" for _ in (*INCIDENT_COLUMNS, "updated_at"))}
                )
                ON CONFLICT(number) DO UPDATE SET
                    {", ".join(
                        f"{column} = excluded.{column}"
                        for column in update_columns
                    )},
                    updated_at = excluded.updated_at
                """,
                [
                    *(values[column] for column in INCIDENT_COLUMNS),
                    now,
                ],
            )

        return payload.number

    async def get(self, number: str) -> dict[str, Any] | None:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            "SELECT * FROM incidents WHERE number = ?",
            (number,),
        )
        row = await cursor.fetchone()

        return dict(row) if row is not None else None

    async def get_many(
        self,
        numbers: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not numbers:
            return {}

        connection = await self._database.read_connection()
        placeholders = ",".join("?" for _ in numbers)

        cursor = await connection.execute(
            f"SELECT * FROM incidents WHERE number IN ({placeholders})",
            numbers,
        )
        rows = await cursor.fetchall()

        return {
            str(row["number"]): dict(row)
            for row in rows
        }