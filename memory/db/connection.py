from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from memory.db.migrations.runner import apply_migrations


class Database:
    """SQLite lifecycle, migrations, and serialized write transactions."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

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

                self._connection = connection
            except Exception:
                await connection.close()
                raise

    async def close(self) -> None:
        connection = self._connection
        self._connection = None

        if connection is not None:
            await connection.close()

    async def read_connection(self) -> aiosqlite.Connection:
        await self.connect()

        if self._connection is None:
            raise RuntimeError("Database connection was not initialized")

        return self._connection

    async def initialize(
        self,
        *,
        schema_path: Path,
        migrations_path: Path,
    ) -> None:
        """
        Apply idempotent base schema and all pending migrations.

        Never add production structural changes to schema.sql after the first
        deployment. Add a versioned migration instead.
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

    async def load_extension(self, extension_loader: object) -> None:
        """Load a Python-provided SQLite extension for this connection."""
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
        connection = await self.read_connection()

        async with self._write_lock:
            await connection.execute("BEGIN IMMEDIATE")

            try:
                yield connection
            except Exception:
                await connection.rollback()
                raise
            else:
                await connection.commit()