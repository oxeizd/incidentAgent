from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from app.memory.db.connection import Database


VectorEntity = Literal["incidents", "assignments"]


@dataclass(frozen=True, slots=True)
class VectorEntityConfig:
    """
    Статическая схема vector storage для одной domain entity.

    Имена таблиц и колонок зашиты в backend config. Они не могут
    формироваться из пользовательского ввода, API или LLM.
    """

    entity: VectorEntity
    domain_table: str
    domain_id_column: str
    vec_table: str
    blob_store_table: str
    blob_store_id_column: str
    label: str


_INCIDENT_CONFIG = VectorEntityConfig(
    entity="incidents",
    domain_table="incidents",
    domain_id_column="number",
    vec_table="incident_vectors",
    blob_store_table="incident_vector_store",
    blob_store_id_column="incident_number",
    label="Incident",
)

_ASSIGNMENT_CONFIG = VectorEntityConfig(
    entity="assignments",
    domain_table="assignments",
    domain_id_column="id",
    vec_table="assignment_vectors",
    blob_store_table="assignment_vector_store",
    blob_store_id_column="assignment_id",
    label="Assignment",
)


class VectorRepository:
    """
    Хранилище embeddings и KNN retrieval.

    Для каждого вектора поддерживаются два синхронных представления:
    - sqlite-vec virtual table для global KNN;
    - обычный BLOB store для hybrid reranking после SQL filters.

    Public incident/assignment methods сохранены как понятный domain API.
    Общая реализация находится в private entity-config helpers.
    """

    def __init__(
        self,
        *,
        database: Database,
        vector_dimension: int,
    ) -> None:
        if vector_dimension < 1:
            raise ValueError(
                "vector_dimension must be at least 1"
            )

        self._database = database
        self._vector_dimension = vector_dimension
        self._entities: dict[VectorEntity, VectorEntityConfig] = {
            "incidents": _INCIDENT_CONFIG,
            "assignments": _ASSIGNMENT_CONFIG,
        }

    async def upsert_incident_vector(
        self,
        *,
        incident_number: str,
        vector: np.ndarray,
    ) -> None:
        await self._upsert_vector(
            entity="incidents",
            entity_id=incident_number,
            vector=vector,
        )

    async def delete_incident_vector(
        self,
        *,
        incident_number: str,
    ) -> None:
        await self._delete_vector(
            entity="incidents",
            entity_id=incident_number,
        )

    async def upsert_assignment_vector(
        self,
        *,
        assignment_id: str,
        vector: np.ndarray,
    ) -> None:
        await self._upsert_vector(
            entity="assignments",
            entity_id=assignment_id,
            vector=vector,
        )

    async def delete_assignment_vector(
        self,
        *,
        assignment_id: str,
    ) -> None:
        await self._delete_vector(
            entity="assignments",
            entity_id=assignment_id,
        )

    async def find_similar_incident_ids(
        self,
        *,
        query_vector: np.ndarray,
        limit: int,
    ) -> list[tuple[str, float]]:
        return await self._find_similar_ids(
            entity="incidents",
            query_vector=query_vector,
            limit=limit,
        )

    async def find_similar_assignment_ids(
        self,
        *,
        query_vector: np.ndarray,
        limit: int,
    ) -> list[tuple[str, float]]:
        return await self._find_similar_ids(
            entity="assignments",
            query_vector=query_vector,
            limit=limit,
        )

    async def get_incident_vectors(
        self,
        *,
        incident_numbers: list[str],
    ) -> dict[str, np.ndarray]:
        return await self._get_vectors(
            entity="incidents",
            entity_ids=incident_numbers,
        )

    async def get_assignment_vectors(
        self,
        *,
        assignment_ids: list[str],
    ) -> dict[str, np.ndarray]:
        return await self._get_vectors(
            entity="assignments",
            entity_ids=assignment_ids,
        )

    async def _upsert_vector(
        self,
        *,
        entity: VectorEntity,
        entity_id: str,
        vector: np.ndarray,
    ) -> None:
        config = self._entities[entity]
        packed = self._pack_vector(vector)

        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name=config.domain_table,
                id_column=config.domain_id_column,
                id_value=entity_id,
            )
            if rowid is None:
                raise ValueError(
                    f"{config.label} does not exist: {entity_id!r}"
                )

            await connection.execute(
                f"DELETE FROM {config.vec_table} WHERE rowid = ?",
                (rowid,),
            )
            await connection.execute(
                (
                    f"INSERT INTO {config.vec_table} "
                    "(rowid, embedding) VALUES (?, ?)"
                ),
                (rowid, packed),
            )

            await connection.execute(
                f"""
                INSERT INTO {config.blob_store_table} (
                    {config.blob_store_id_column},
                    embedding,
                    dimension
                )
                VALUES (?, ?, ?)
                ON CONFLICT({config.blob_store_id_column}) DO UPDATE SET
                    embedding = excluded.embedding,
                    dimension = excluded.dimension
                """,
                (
                    entity_id,
                    packed,
                    self._vector_dimension,
                ),
            )

    async def _delete_vector(
        self,
        *,
        entity: VectorEntity,
        entity_id: str,
    ) -> None:
        config = self._entities[entity]

        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name=config.domain_table,
                id_column=config.domain_id_column,
                id_value=entity_id,
            )

            if rowid is not None:
                await connection.execute(
                    f"DELETE FROM {config.vec_table} WHERE rowid = ?",
                    (rowid,),
                )

            await connection.execute(
                f"""
                DELETE FROM {config.blob_store_table}
                WHERE {config.blob_store_id_column} = ?
                """,
                (entity_id,),
            )

    async def _find_similar_ids(
        self,
        *,
        entity: VectorEntity,
        query_vector: np.ndarray,
        limit: int,
    ) -> list[tuple[str, float]]:
        config = self._entities[entity]
        packed = self._pack_vector(query_vector)
        bounded_limit = _normalize_knn_limit(limit)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT
                d.{config.domain_id_column} AS entity_id,
                v.distance AS distance
            FROM {config.vec_table} AS v
            JOIN {config.domain_table} AS d ON d.rowid = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance ASC, d.{config.domain_id_column} ASC
            """,
            (packed, bounded_limit),
        )
        rows = await cursor.fetchall()

        return [
            (str(row["entity_id"]), float(row["distance"]))
            for row in rows
        ]

    async def _get_vectors(
        self,
        *,
        entity: VectorEntity,
        entity_ids: Sequence[str],
    ) -> dict[str, np.ndarray]:
        normalized_ids = _normalize_entity_ids(entity_ids)
        if not normalized_ids:
            return {}

        config = self._entities[entity]
        placeholders = ",".join("?" for _ in normalized_ids)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            f"""
            SELECT
                {config.blob_store_id_column} AS entity_id,
                embedding,
                dimension
            FROM {config.blob_store_table}
            WHERE {config.blob_store_id_column} IN ({placeholders})
            """,
            normalized_ids,
        )
        rows = await cursor.fetchall()

        return {
            str(row["entity_id"]): self._unpack_vector(
                packed=row["embedding"],
                dimension=int(row["dimension"]),
            )
            for row in rows
        }

    def _pack_vector(
        self,
        vector: np.ndarray,
    ) -> bytes:
        normalized = np.asarray(vector, dtype=np.float32)

        if normalized.ndim != 1:
            raise ValueError(
                "Vector must be one-dimensional, "
                f"got shape {normalized.shape}"
            )

        if normalized.size != self._vector_dimension:
            raise ValueError(
                "Vector dimension does not match configured dimension: "
                f"expected {self._vector_dimension}, "
                f"got {normalized.size}"
            )

        if not np.all(np.isfinite(normalized)):
            raise ValueError("Vector must contain only finite values")

        return normalized.tobytes()

    def _unpack_vector(
        self,
        *,
        packed: bytes,
        dimension: int,
    ) -> np.ndarray:
        if dimension != self._vector_dimension:
            raise ValueError(
                "Stored vector dimension does not match configured "
                f"dimension: expected {self._vector_dimension}, "
                f"got {dimension}"
            )

        vector = np.frombuffer(packed, dtype=np.float32)

        if vector.size != dimension:
            raise ValueError(
                "Stored embedding byte length does not match dimension: "
                f"expected {dimension}, got {vector.size}"
            )

        if not np.all(np.isfinite(vector)):
            raise ValueError(
                "Stored vector must contain only finite values"
            )

        return vector.copy()


async def _find_rowid(
    *,
    connection: Any,
    table_name: str,
    id_column: str,
    id_value: str,
) -> int | None:
    cursor = await connection.execute(
        f"""
        SELECT rowid
        FROM {table_name}
        WHERE {id_column} = ?
        """,
        (id_value,),
    )
    row = await cursor.fetchone()

    return int(row["rowid"]) if row is not None else None


def _normalize_entity_ids(
    values: Sequence[str],
) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        )
    )


def _normalize_knn_limit(
    value: int,
) -> int:
    if value < 1:
        raise ValueError("KNN limit must be at least 1")

    return min(value, 1_000)