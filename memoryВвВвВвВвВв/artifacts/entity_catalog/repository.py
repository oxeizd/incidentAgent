from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from app.memory.artifacts.catalog.contracts import (
    EntityCatalogEntry,
    EntityCatalogType,
    EntityCatalogUpsert,
)
from app.memory.db.connection import Database
from app.memory.utils import utc_now_iso


class EntityCatalogRepository:
    """
    Persistent storage for canonical incident entities.

    Repository owns atomic CRUD and bulk replacement. Fuzzy scoring,
    cache management and ambiguity decisions belong to EntityCatalogResolver.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert(
        self,
        *,
        entry: EntityCatalogUpsert,
        normalized_value: str,
    ) -> EntityCatalogEntry:
        now = utc_now_iso()
        entry_id = f"catalog-{uuid.uuid4().hex}"

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO entity_catalog (
                    id,
                    entity_type,
                    canonical_value,
                    normalized_value,
                    aliases_json,
                    source_count,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, canonical_value) DO UPDATE SET
                    normalized_value = excluded.normalized_value,
                    aliases_json = excluded.aliases_json,
                    source_count = excluded.source_count,
                    updated_at = excluded.updated_at
                """,
                (
                    entry_id,
                    entry.entity_type,
                    entry.canonical_value,
                    normalized_value,
                    _serialize_aliases(entry.aliases),
                    entry.source_count,
                    now,
                    now,
                ),
            )

            cursor = await connection.execute(
                """
                SELECT *
                FROM entity_catalog
                WHERE entity_type = ?
                  AND canonical_value = ?
                """,
                (
                    entry.entity_type,
                    entry.canonical_value,
                ),
            )
            row = await cursor.fetchone()

        if row is None:
            raise RuntimeError(
                "Entity catalog entry disappeared after upsert"
            )

        return _row_to_entry(row)

    async def replace_type(
        self,
        *,
        entity_type: EntityCatalogType,
        entries: Sequence[tuple[EntityCatalogUpsert, str]],
    ) -> int:
        """
        Replaces all entries for one entity type in a single transaction.

        Intended for complete derived rebuild from the current incidents
        dataset. It removes stale values and recalculates source_count.
        """
        now = utc_now_iso()

        values = [
            (
                f"catalog-{uuid.uuid4().hex}",
                entry.entity_type,
                entry.canonical_value,
                normalized_value,
                _serialize_aliases(entry.aliases),
                entry.source_count,
                now,
                now,
            )
            for entry, normalized_value in entries
        ]

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                DELETE FROM entity_catalog
                WHERE entity_type = ?
                """,
                (entity_type,),
            )

            if values:
                await connection.executemany(
                    """
                    INSERT INTO entity_catalog (
                        id,
                        entity_type,
                        canonical_value,
                        normalized_value,
                        aliases_json,
                        source_count,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )

        return len(values)

    async def get(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> EntityCatalogEntry | None:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM entity_catalog
            WHERE entity_type = ?
              AND canonical_value = ?
            """,
            (entity_type, canonical_value),
        )
        row = await cursor.fetchone()

        return _row_to_entry(row) if row is not None else None

    async def list_by_type(
        self,
        *,
        entity_type: EntityCatalogType,
        limit: int | None = None,
    ) -> list[EntityCatalogEntry]:
        connection = await self._database.read_connection()

        if limit is None:
            cursor = await connection.execute(
                """
                SELECT *
                FROM entity_catalog
                WHERE entity_type = ?
                ORDER BY source_count DESC, canonical_value ASC
                """,
                (entity_type,),
            )
        else:
            if limit < 1:
                raise ValueError("limit must be at least 1")

            cursor = await connection.execute(
                """
                SELECT *
                FROM entity_catalog
                WHERE entity_type = ?
                ORDER BY source_count DESC, canonical_value ASC
                LIMIT ?
                """,
                (entity_type, min(limit, 10_000)),
            )

        rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    async def list_all(self) -> list[EntityCatalogEntry]:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT *
            FROM entity_catalog
            ORDER BY
                entity_type ASC,
                source_count DESC,
                canonical_value ASC
            """
        )
        rows = await cursor.fetchall()

        return [_row_to_entry(row) for row in rows]

    async def delete(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM entity_catalog
                WHERE entity_type = ?
                  AND canonical_value = ?
                """,
                (entity_type, canonical_value),
            )

        return cursor.rowcount > 0

    async def count(self) -> int:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM entity_catalog"
        )
        row = await cursor.fetchone()

        return int(row["count"]) if row is not None else 0


def _row_to_entry(row: Any) -> EntityCatalogEntry:
    return EntityCatalogEntry(
        id=str(row["id"]),
        entity_type=str(row["entity_type"]),
        canonical_value=str(row["canonical_value"]),
        normalized_value=str(row["normalized_value"]),
        aliases=_deserialize_aliases(row["aliases_json"]),
        source_count=int(row["source_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _serialize_aliases(values: list[str]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_aliases(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []

    if not isinstance(decoded, list):
        return []

    return [
        item.strip()
        for item in decoded
        if isinstance(item, str) and item.strip()
    ]