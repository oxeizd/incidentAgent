import logging
import aiosqlite
from pathlib import Path
from app.config import memory_settings

logger = logging.getLogger(__name__)
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        db_path: Path = memory_settings.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA temp_store = MEMORY")
        if memory_settings.sqlite_vec_enabled:
            import sqlite_vec
            await conn.enable_load_extension(True)
            await conn._execute(sqlite_vec.load, conn._conn)
            await conn.enable_load_extension(False)
            cur = await conn.execute("SELECT vec_version()")
            ver = (await cur.fetchone())[0]
            logger.info("sqlite-vec loaded, version=%s", ver)
        _db = conn
        logger.info("Connected to DB: %s", db_path)
    return _db


async def close_db() -> None:
    """Единственное место, физически закрывающее соединение."""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("DB connection closed")


async def apply_schema(conn: aiosqlite.Connection) -> None:
    schema_path = memory_settings.schema_path
    if schema_path.exists():
        await conn.executescript(schema_path.read_text(encoding="utf-8"))
        logger.info("Schema applied from %s", schema_path)
    else:
        logger.warning("Schema file not found: %s", schema_path)


async def apply_migrations(migrations_dir: Path | None = None) -> None:
    conn = await get_db()
    if migrations_dir is None:
        migrations_dir = memory_settings.migrations_path
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    cur = await conn.execute("SELECT MAX(version) FROM schema_version")
    row = await cur.fetchone()
    current_version: int = row[0] if row[0] is not None else 0
    if not migrations_dir.exists():
        logger.info("Migrations directory not found: %s", migrations_dir)
        return
    for file in sorted(f for f in migrations_dir.iterdir() if f.suffix == ".sql"):
        try:
            version = int(file.stem.split("_")[0])
        except (ValueError, IndexError):
            logger.warning("Skipping migration with unrecognised name: %s", file.name)
            continue
        if version <= current_version:
            continue
        logger.info("Applying migration %s", file.name)
        await conn.executescript(file.read_text(encoding="utf-8"))
        await conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (version,))
        await conn.commit()
    logger.info("Migrations up to date. Current version: %d", current_version)


async def ensure_vector_tables(conn: aiosqlite.Connection | None = None) -> None:
    if conn is None:
        conn = await get_db()
    dim = memory_settings.vector_dim
    for name in ("incident_vec", "assignment_vec"):
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        if await cur.fetchone() is None:
            await conn.execute(f"CREATE VIRTUAL TABLE {name} USING vec0(embedding float[{dim}])")
            logger.info("Created vector table: %s", name)


async def initialize_database() -> None:
    conn = await get_db()
    await apply_schema(conn)
    await apply_migrations()
    if memory_settings.sqlite_vec_enabled:
        await ensure_vector_tables(conn)
    await conn.commit()
    if memory_settings.auto_backfill_vectors:
        await backfill_vectors()
        logger.info("Vector backfill completed")


async def backfill_vectors() -> None:
    from app.memory.repository.vectors import upsert_incident_vector, upsert_assignment_vector
    conn = await get_db()
    cur = await conn.execute("""
        SELECT i.number, i.reason_inc FROM incidents i
        LEFT JOIN incident_vec v ON i.rowid = v.rowid WHERE v.rowid IS NULL
    """)
    rows = await cur.fetchall()
    for number, reason in rows:
        await upsert_incident_vector(number, reason)
    logger.info("Backfilled %d incident vectors", len(rows))
    cur = await conn.execute("""
        SELECT a.id, a.assignment FROM assignments a
        LEFT JOIN assignment_vec v ON a.id = v.rowid WHERE v.rowid IS NULL
    """)
    rows = await cur.fetchall()
    for id_, ass in rows:
        await upsert_assignment_vector(id_, ass)
    logger.info("Backfilled %d assignment vectors", len(rows))