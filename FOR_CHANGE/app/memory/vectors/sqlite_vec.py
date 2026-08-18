from __future__ import annotations

import sqlite_vec

from memory.db.connection import Database


async def initialize_sqlite_vec(
    *,
    database: Database,
    vector_dimension: int,
) -> None:
    """
    Load sqlite-vec and create vector virtual tables.

    Tables are tied to domain-table SQLite rowid values:
    - incident_vectors.rowid = incidents.rowid
    - assignment_vectors.rowid = assignments.rowid
    """
    if vector_dimension < 1:
        raise ValueError("vector_dimension must be at least 1")

    await database.load_extension(sqlite_vec.load)

    connection = await database.read_connection()

    await connection.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS incident_vectors
        USING vec0(embedding float[{vector_dimension}])
        """
    )
    await connection.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS assignment_vectors
        USING vec0(embedding float[{vector_dimension}])
        """
    )
    await connection.commit()