from __future__ import annotations

import logging
import re
from pathlib import Path

import aiosqlite


logger = logging.getLogger(__name__)


_MIGRATION_RE = re.compile(r"^(?P<version>\d{3,})_[a-z0-9_]+\.sql$")


async def apply_migrations(
    connection: aiosqlite.Connection,
    migrations_path: Path,
) -> None:
    """
    Apply SQL migrations exactly once in ascending numeric order.

    Base schema is applied before this function. Migrations are only for
    structural changes after the first database creation.
    """
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )

    if not migrations_path.exists():
        return

    cursor = await connection.execute(
        "SELECT version FROM schema_migrations"
    )
    applied_versions = {
        int(row["version"])
        for row in await cursor.fetchall()
    }

    migrations = _find_migrations(migrations_path)

    for version, path in migrations:
        if version in applied_versions:
            continue

        script = path.read_text(encoding="utf-8")

        try:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.executescript(script)
            await connection.execute(
                """
                INSERT INTO schema_migrations (version, name)
                VALUES (?, ?)
                """,
                (version, path.name),
            )
            await connection.commit()
        except Exception:
            logger.exception(
                "Migration failed: version=%s path=%s",
                version,
                path,
            )
            await connection.rollback()
            raise


def _find_migrations(
    migrations_path: Path,
) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []

    for path in migrations_path.glob("*.sql"):
        match = _MIGRATION_RE.match(path.name)

        if match is None:
            raise ValueError(
                "Invalid migration name. Expected "
                f"NNN_description.sql, got: {path.name}"
            )

        found.append((int(match.group("version")), path))

    found.sort(key=lambda item: item[0])

    versions = [version for version, _ in found]
    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate migration version detected")

    return found