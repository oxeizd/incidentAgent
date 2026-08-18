from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from memory.artifacts.presentations.contracts import PresentationRecord
from memory.artifacts.presentations.document import PresentationDocument
from memory.db.connection import Database


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize_document(document: PresentationDocument) -> str:
    return document.model_dump_json()


def _deserialize_document(value: Any) -> PresentationDocument:
    if isinstance(value, PresentationDocument):
        return value

    if isinstance(value, Mapping):
        return PresentationDocument.model_validate(dict(value))

    if not isinstance(value, str) or not value.strip():
        return PresentationDocument()

    return PresentationDocument.model_validate_json(value)


def _row_to_record(row: Any) -> PresentationRecord:
    data = dict(row)
    data["fields"] = _deserialize_document(data.get("fields"))

    snapshot = data.get("published_snapshot")
    data["published_snapshot"] = (
        _deserialize_document(snapshot)
        if snapshot is not None
        else None
    )

    return PresentationRecord.model_validate(data)


class PresentationRepository:
    """
    Store complete typed presentation documents.

    `fields` is the editable draft document.
    `published_snapshot` is an immutable copy created during publication.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        fields: PresentationDocument,
    ) -> str:
        presentation_id = f"presentation-{uuid.uuid4().hex[:12]}"
        now = _utc_now_iso()

        async with self.database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO presentations (
                    id,
                    owner_user_id,
                    thread_id,
                    status,
                    fields,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 'draft', ?, ?, ?)
                """,
                (
                    presentation_id,
                    owner_user_id,
                    thread_id,
                    _serialize_document(fields),
                    now,
                    now,
                ),
            )

        return presentation_id

    async def get(
        self,
        presentation_id: str,
    ) -> PresentationRecord | None:
        connection = await self.database.read_connection()

        cursor = await connection.execute(
            "SELECT * FROM presentations WHERE id = ?",
            (presentation_id,),
        )
        row = await cursor.fetchone()

        return _row_to_record(row) if row is not None else None

    async def list_mine(
        self,
        owner_user_id: str,
    ) -> list[PresentationRecord]:
        connection = await self.database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM presentations
            WHERE owner_user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (owner_user_id,),
        )
        rows = await cursor.fetchall()

        return [_row_to_record(row) for row in rows]

    async def list_shared(self) -> list[PresentationRecord]:
        connection = await self.database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM presentations
            WHERE status = 'published'
            ORDER BY published_at DESC, id DESC
            """
        )
        rows = await cursor.fetchall()

        return [_row_to_record(row) for row in rows]

    async def update_fields(
        self,
        presentation_id: str,
        owner_user_id: str,
        fields: PresentationDocument,
    ) -> bool:
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE presentations
                SET fields = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    _serialize_document(fields),
                    _utc_now_iso(),
                    presentation_id,
                    owner_user_id,
                ),
            )

        return cursor.rowcount > 0

    async def publish(
        self,
        presentation_id: str,
        owner_user_id: str,
    ) -> bool:
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT fields
                FROM presentations
                WHERE id = ? AND owner_user_id = ?
                """,
                (presentation_id, owner_user_id),
            )
            row = await cursor.fetchone()

            if row is None:
                return False

            document = _deserialize_document(row["fields"])
            snapshot = _serialize_document(document)
            now = _utc_now_iso()

            await connection.execute(
                """
                UPDATE presentations
                SET
                    status = 'published',
                    published_snapshot = ?,
                    published_at = ?,
                    updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    snapshot,
                    now,
                    now,
                    presentation_id,
                    owner_user_id,
                ),
            )

        return True

    async def unpublish(
        self,
        presentation_id: str,
        owner_user_id: str,
    ) -> bool:
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE presentations
                SET status = 'draft', updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    _utc_now_iso(),
                    presentation_id,
                    owner_user_id,
                ),
            )

        return cursor.rowcount > 0

    async def delete(
        self,
        presentation_id: str,
        owner_user_id: str,
    ) -> bool:
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM presentations
                WHERE id = ? AND owner_user_id = ?
                """,
                (presentation_id, owner_user_id),
            )

        return cursor.rowcount > 0