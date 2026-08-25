from __future__ import annotations

import sqlite_vec

from app.memory.db.connection import Database


_VECTOR_TABLES = (
    "incident_vectors",
    "assignment_vectors",
)


async def initialize_sqlite_vec(
    *,
    database: Database,
    vector_dimension: int,
) -> None:
    """
    Loads sqlite-vec and creates vector virtual tables.

    rowid у vec0 совпадает с rowid domain record:
    - incident_vectors.rowid = incidents.rowid
    - assignment_vectors.rowid = assignments.rowid.

    Изменение vector_dimension требует осознанной миграции/rebuild:
    vec0 table нельзя прозрачно переопределить через CREATE IF NOT EXISTS.
    """
    if vector_dimension < 1:
        raise ValueError("vector_dimension must be at least 1")

    await database.load_extension(sqlite_vec.load)

    connection = await database.read_connection()

    for table_name in _VECTOR_TABLES:
        await connection.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}
            USING vec0(embedding float[{vector_dimension}])
            """
        )

    await connection.commit()