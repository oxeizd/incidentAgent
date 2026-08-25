from __future__ import annotations

from typing import Any

from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.db.connection import Database
from app.memory.utils import (
    datetime_to_iso,
    normalize_ids,
    placeholders,
    utc_now_iso,
)


_INCIDENT_COLUMNS: tuple[str, ...] = (
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

_DATETIME_COLUMNS = (
    "created_at",
    "target_date",
    "plan_finish_date",
    "close_date",
    "detection_time",
    "start_time",
    "end_time",
)

_BOOLEAN_COLUMNS = (
    "impact_custom_service",
    "no_impact",
    "is_root",
)


class IncidentRepository:
    """Persistence for independent incident artifacts."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert(self, payload: IncidentUpsert) -> str:
        values = payload.model_dump(mode="python")

        for field_name in _DATETIME_COLUMNS:
            values[field_name] = datetime_to_iso(
                values[field_name]
            )

        for field_name in _BOOLEAN_COLUMNS:
            values[field_name] = int(values[field_name])

        now = utc_now_iso()
        update_columns = tuple(
            column
            for column in _INCIDENT_COLUMNS
            if column != "number"
        )

        async with self._database.transaction() as connection:
            await connection.execute(
                f"""
                INSERT INTO incidents (
                    {", ".join(_INCIDENT_COLUMNS)},
                    updated_at
                )
                VALUES (
                    {", ".join("?" for _ in _INCIDENT_COLUMNS)},
                    ?
                )
                ON CONFLICT(number) DO UPDATE SET
                    {", ".join(
                        f"{column} = excluded.{column}"
                        for column in update_columns
                    )},
                    updated_at = excluded.updated_at
                """,
                (
                    *(
                        values[column]
                        for column in _INCIDENT_COLUMNS
                    ),
                    now,
                ),
            )

        return payload.number

    async def get(
        self,
        number: str,
    ) -> dict[str, Any] | None:
        normalized = _require_id(number, field_name="number")
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM incidents
            WHERE number = ?
            """,
            (normalized,),
        )
        row = await cursor.fetchone()

        return dict(row) if row is not None else None

    async def get_many(
        self,
        numbers: list[str],
    ) -> dict[str, dict[str, Any]]:
        normalized_numbers = normalize_ids(numbers)

        if not normalized_numbers:
            return {}

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT *
            FROM incidents
            WHERE number IN ({placeholders(normalized_numbers)})
            """,
            normalized_numbers,
        )
        rows = await cursor.fetchall()

        return {
            str(row["number"]): dict(row)
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