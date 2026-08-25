from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.memory.artifacts.incident_reports.document import (
    IncidentReportRecord,
    IncidentReportStatus,
    IncidentReportVersion,
)
from app.memory.artifacts.incident_reports.errors import (
    IncidentReportVersionConflictError,
)
from app.memory.db.connection import Database
from app.memory.utils import (
    normalize_ids,
    placeholders,
    utc_now_iso,
)


def _serialize_sections(sections: dict[str, Any]) -> str:
    return json.dumps(
        sections,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_sections(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Incident report sections must be a JSON object"
        )

    decoded = json.loads(value)

    if not isinstance(decoded, dict):
        raise ValueError(
            "Incident report sections must be a JSON object"
        )

    return decoded


def _row_to_version(row: Any) -> IncidentReportVersion:
    data = dict(row)
    data["sections"] = _deserialize_sections(
        data.pop("sections_json")
    )

    return IncidentReportVersion.model_validate(data)


def _row_to_record(
    row: Any,
    *,
    versions: Sequence[IncidentReportVersion],
) -> IncidentReportRecord:
    return IncidentReportRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        thread_id=str(row["thread_id"]),
        status=str(row["status"]),
        current_version=int(row["current_version"]),
        versions=list(versions),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class IncidentReportRepository:
    """
    Persistent immutable-version storage for RCA reports.

    incident_reports stores metadata/current version.
    incident_report_versions stores immutable section snapshots.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        sections: dict[str, Any],
        created_by_task_id: str,
        status: IncidentReportStatus = "draft",
        note: str | None = None,
    ) -> IncidentReportRecord:
        report_id = f"incident-report-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO incident_reports (
                    id,
                    owner_user_id,
                    thread_id,
                    status,
                    current_version,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    owner_user_id,
                    thread_id,
                    status,
                    0,
                    now,
                    now,
                ),
            )
            await connection.execute(
                """
                INSERT INTO incident_report_versions (
                    report_id,
                    version,
                    sections_json,
                    created_at,
                    created_by_task_id,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    0,
                    _serialize_sections(sections),
                    now,
                    created_by_task_id,
                    note,
                ),
            )

        return IncidentReportRecord(
            id=report_id,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            status=status,
            current_version=0,
            versions=[
                IncidentReportVersion(
                    version=0,
                    sections=sections,
                    created_at=now,
                    created_by_task_id=created_by_task_id,
                    note=note,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    async def get(
        self,
        report_id: str,
    ) -> IncidentReportRecord | None:
        reports = await self._get_many_by_ids([report_id])
        return reports[0] if reports else None

    async def list_mine(
        self,
        *,
        owner_user_id: str,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[IncidentReportRecord]:
        normalized_limit = min(max(limit, 1), 200)
        connection = await self._database.read_connection()

        if thread_id is None:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    owner_user_id,
                    thread_id,
                    status,
                    current_version,
                    created_at,
                    updated_at
                FROM incident_reports
                WHERE owner_user_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (owner_user_id, normalized_limit),
            )
        else:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    owner_user_id,
                    thread_id,
                    status,
                    current_version,
                    created_at,
                    updated_at
                FROM incident_reports
                WHERE owner_user_id = ?
                  AND thread_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (
                    owner_user_id,
                    thread_id,
                    normalized_limit,
                ),
            )

        report_rows = await cursor.fetchall()

        return await self._build_records(
            report_rows=report_rows,
            preserve_report_order=True,
        )

    async def append_version(
        self,
        *,
        report_id: str,
        expected_version: int,
        sections: dict[str, Any],
        created_by_task_id: str,
        note: str | None = None,
        status: IncidentReportStatus | None = None,
    ) -> IncidentReportRecord:
        next_version = expected_version + 1
        now = utc_now_iso()

        async with self._database.transaction() as connection:
            if status is None:
                cursor = await connection.execute(
                    """
                    UPDATE incident_reports
                    SET
                        current_version = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND current_version = ?
                    """,
                    (
                        next_version,
                        now,
                        report_id,
                        expected_version,
                    ),
                )
            else:
                cursor = await connection.execute(
                    """
                    UPDATE incident_reports
                    SET
                        status = ?,
                        current_version = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND current_version = ?
                    """,
                    (
                        status,
                        next_version,
                        now,
                        report_id,
                        expected_version,
                    ),
                )

            if cursor.rowcount != 1:
                raise IncidentReportVersionConflictError(
                    "Incident report has changed since preview creation"
                )

            await connection.execute(
                """
                INSERT INTO incident_report_versions (
                    report_id,
                    version,
                    sections_json,
                    created_at,
                    created_by_task_id,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    next_version,
                    _serialize_sections(sections),
                    now,
                    created_by_task_id,
                    note,
                ),
            )

        report = await self.get(report_id)

        if report is None:
            raise RuntimeError(
                "Incident report disappeared after version append"
            )

        return report

    async def _get_many_by_ids(
        self,
        report_ids: Sequence[str],
    ) -> list[IncidentReportRecord]:
        normalized_ids = normalize_ids(report_ids)

        if not normalized_ids:
            return []

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT
                id,
                owner_user_id,
                thread_id,
                status,
                current_version,
                created_at,
                updated_at
            FROM incident_reports
            WHERE id IN ({placeholders(normalized_ids)})
            """,
            normalized_ids,
        )
        report_rows = await cursor.fetchall()

        return await self._build_records(
            report_rows=report_rows,
            preserve_report_order=False,
        )

    async def _build_records(
        self,
        *,
        report_rows: Sequence[Any],
        preserve_report_order: bool,
    ) -> list[IncidentReportRecord]:
        if not report_rows:
            return []

        report_ids = [
            str(row["id"])
            for row in report_rows
        ]
        versions_by_report_id = await self._get_versions_by_report_ids(
            report_ids
        )

        records = [
            _row_to_record(
                row,
                versions=versions_by_report_id.get(
                    str(row["id"]),
                    [],
                ),
            )
            for row in report_rows
        ]

        if preserve_report_order:
            return records

        return sorted(
            records,
            key=lambda report: report.id,
        )

    async def _get_versions_by_report_ids(
        self,
        report_ids: Sequence[str],
    ) -> dict[str, list[IncidentReportVersion]]:
        normalized_ids = normalize_ids(report_ids)

        if not normalized_ids:
            return {}

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT
                report_id,
                version,
                sections_json,
                created_at,
                created_by_task_id,
                note
            FROM incident_report_versions
            WHERE report_id IN ({placeholders(normalized_ids)})
            ORDER BY report_id ASC, version ASC
            """,
            normalized_ids,
        )
        rows = await cursor.fetchall()

        result: dict[str, list[IncidentReportVersion]] = defaultdict(list)

        for row in rows:
            result[str(row["report_id"])].append(
                _row_to_version(row)
            )

        return dict(result)