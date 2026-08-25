from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.memory.db.migrations.runner import apply_migrations


logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 10_000


class Database:
    """
    SQLite lifecycle, migrations and serialized write transactions.

    This implementation intentionally uses one aiosqlite connection per
    application process. SQLite permits concurrent reads under WAL, while
    _write_lock serializes write transactions and prevents close() from
    closing the connection during an active write.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def connect(self) -> None:
        if self._connection is not None:
            return

        async with self._connect_lock:
            if self._connection is not None:
                return

            self._path.parent.mkdir(parents=True, exist_ok=True)

            connection = await aiosqlite.connect(str(self._path))
            connection.row_factory = aiosqlite.Row

            try:
                await connection.execute("PRAGMA foreign_keys = ON")
                await connection.execute("PRAGMA journal_mode = WAL")
                await connection.execute("PRAGMA synchronous = NORMAL")
                await connection.execute("PRAGMA temp_store = MEMORY")
                await connection.execute(
                    f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}"
                )
                await connection.commit()

                self._connection = connection
            except Exception:
                logger.exception(
                    "Failed to initialize SQLite connection: %s",
                    self._path,
                )
                await connection.close()
                raise

    async def close(self) -> None:
        """
        Closes the shared connection only after active write work completes.

        A later read_connection() may initialize a new connection. This
        preserves the existing reconnect behavior while preventing a close
        race with an active transaction.
        """
        async with self._write_lock:
            connection = self._connection
            self._connection = None

            if connection is not None:
                await connection.close()

    async def read_connection(self) -> aiosqlite.Connection:
        await self.connect()

        connection = self._connection
        if connection is None:
            raise RuntimeError("Database connection was not initialized")

        return connection

    async def initialize(
        self,
        *,
        schema_path: Path,
        migrations_path: Path,
    ) -> None:
        """
        Applies idempotent base schema and then versioned migrations.

        New production changes must use migrations. schema.sql remains the
        complete bootstrap schema for a fresh database.
        """
        connection = await self.read_connection()

        if not schema_path.exists():
            raise FileNotFoundError(
                f"Schema file not found: {schema_path}"
            )

        await connection.executescript(
            schema_path.read_text(encoding="utf-8")
        )
        await connection.commit()

        await apply_migrations(
            connection,
            migrations_path,
        )
        await connection.commit()

    async def load_extension(
        self,
        extension_loader: object,
    ) -> None:
        """
        Loads a Python-provided SQLite extension into the shared connection.

        sqlite-vec uses this during startup. Extensions are never selected
        by external input.
        """
        connection = await self.read_connection()

        await connection.enable_load_extension(True)

        try:
            await connection._execute(
                extension_loader,
                connection._conn,
            )
        finally:
            await connection.enable_load_extension(False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Provides one serialized SQLite write transaction.

        BEGIN IMMEDIATE, the caller body and commit are all protected by
        _write_lock. If BEGIN itself fails, the failure is logged and the
        connection is not rolled back because no transaction started.
        """
        connection = await self.read_connection()

        async with self._write_lock:
            started = False

            try:
                await connection.execute("BEGIN IMMEDIATE")
                started = True

                yield connection
            except Exception:
                logger.exception(
                    "SQLite transaction failed: %s",
                    self._path,
                )

                if started:
                    try:
                        await connection.rollback()
                    except Exception:
                        logger.exception(
                            "SQLite transaction rollback failed: %s",
                            self._path,
                        )

                raise
            else:
                try:
                    await connection.commit()
                except Exception:
                    logger.exception(
                        "SQLite transaction commit failed: %s",
                        self._path,
                    )

                    try:
                        await connection.rollback()
                    except Exception:
                        logger.exception(
                            "SQLite transaction rollback after "
                            "commit failure failed: %s",
                            self._path,
                        )

                    raise