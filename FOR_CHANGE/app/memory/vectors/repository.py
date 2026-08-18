from __future__ import annotations

from typing import Any

import numpy as np

from memory.db.connection import Database


class VectorRepository:
    """
    sqlite-vec storage and KNN retrieval.

    Vector rowid always equals the hidden SQLite rowid of the corresponding
    independent domain artifact. This class stores only vectors and IDs; it
    never invokes the embedding provider.
    """

    def __init__(
        self,
        *,
        database: Database,
        vector_dimension: int,
    ) -> None:
        if vector_dimension < 1:
            raise ValueError("vector_dimension must be at least 1")

        self._database = database
        self._vector_dimension = vector_dimension

    async def upsert_incident_vector(
        self,
        *,
        incident_number: str,
        vector: np.ndarray,
    ) -> None:
        packed = self._pack_vector(vector)

        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name="incidents",
                id_column="number",
                id_value=incident_number,
            )

            if rowid is None:
                raise ValueError(
                    f"Incident does not exist: {incident_number!r}"
                )

            await connection.execute(
                """
                INSERT OR REPLACE INTO incident_vectors (rowid, embedding)
                VALUES (?, ?)
                """,
                (rowid, packed),
            )

    async def delete_incident_vector(
        self,
        *,
        incident_number: str,
    ) -> None:
        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name="incidents",
                id_column="number",
                id_value=incident_number,
            )

            if rowid is None:
                return

            await connection.execute(
                "DELETE FROM incident_vectors WHERE rowid = ?",
                (rowid,),
            )

    async def upsert_assignment_vector(
        self,
        *,
        assignment_id: str,
        vector: np.ndarray,
    ) -> None:
        packed = self._pack_vector(vector)

        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name="assignments",
                id_column="id",
                id_value=assignment_id,
            )

            if rowid is None:
                raise ValueError(
                    f"Assignment does not exist: {assignment_id!r}"
                )

            await connection.execute(
                """
                INSERT OR REPLACE INTO assignment_vectors (rowid, embedding)
                VALUES (?, ?)
                """,
                (rowid, packed),
            )

    async def delete_assignment_vector(
        self,
        *,
        assignment_id: str,
    ) -> None:
        async with self._database.transaction() as connection:
            rowid = await _find_rowid(
                connection=connection,
                table_name="assignments",
                id_column="id",
                id_value=assignment_id,
            )

            if rowid is None:
                return

            await connection.execute(
                "DELETE FROM assignment_vectors WHERE rowid = ?",
                (rowid,),
            )

    async def find_similar_incident_ids(
        self,
        *,
        query_vector: np.ndarray,
        limit: int,
    ) -> list[tuple[str, float]]:
        """
        Return incident numbers ordered by ascending vector distance.
        """
        packed = self._pack_vector(query_vector)
        bounded_limit = _normalize_knn_limit(limit)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            """
            SELECT i.number AS entity_id, v.distance AS distance
            FROM incident_vectors AS v
            JOIN incidents AS i ON i.rowid = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance ASC
            """,
            (packed, bounded_limit),
        )
        rows = await cursor.fetchall()

        return [
            (str(row["entity_id"]), float(row["distance"]))
            for row in rows
        ]

    async def find_similar_assignment_ids(
        self,
        *,
        query_vector: np.ndarray,
        limit: int,
    ) -> list[tuple[str, float]]:
        """
        Return assignment IDs ordered by ascending vector distance.
        """
        packed = self._pack_vector(query_vector)
        bounded_limit = _normalize_knn_limit(limit)

        connection = await self._database.read_connection()
        cursor = await connection.execute(
            """
            SELECT a.id AS entity_id, v.distance AS distance
            FROM assignment_vectors AS v
            JOIN assignments AS a ON a.rowid = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance ASC
            """,
            (packed, bounded_limit),
        )
        rows = await cursor.fetchall()

        return [
            (str(row["entity_id"]), float(row["distance"]))
            for row in rows
        ]

    def _pack_vector(self, vector: np.ndarray) -> bytes:
        normalized = np.asarray(vector, dtype=np.float32)

        if normalized.ndim != 1:
            raise ValueError(
                f"Vector must be one-dimensional, got shape {normalized.shape}"
            )

        if normalized.size != self._vector_dimension:
            raise ValueError(
                "Vector dimension does not match configured dimension: "
                f"expected {self._vector_dimension}, got {normalized.size}"
            )

        return normalized.tobytes()


async def _find_rowid(
    *,
    connection: Any,
    table_name: str,
    id_column: str,
    id_value: str,
) -> int | None:
    """
    Find domain rowid.

    table_name/id_column are constants supplied only by VectorRepository,
    never user input; interpolation is therefore safe.
    """
    cursor = await connection.execute(
        f"SELECT rowid FROM {table_name} WHERE {id_column} = ?",
        (id_value,),
    )
    row = await cursor.fetchone()

    return int(row["rowid"]) if row is not None else None


def _normalize_knn_limit(value: int) -> int:
    if value < 1:
        raise ValueError("KNN limit must be at least 1")

    return min(value, 1_000)