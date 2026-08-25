from __future__ import annotations

import asyncio
import logging

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.memory.application import MemoryApplication

logger = logging.getLogger(__name__)

_checkpointer: AsyncSqliteSaver | None = None
_checkpointer_lock = asyncio.Lock()


async def get_checkpointer(
    memory_application: MemoryApplication,
) -> AsyncSqliteSaver:
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    async with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer

        connection = await memory_application.database.read_connection()

        checkpointer = AsyncSqliteSaver(connection)
        await checkpointer.setup()

        _checkpointer = checkpointer

        logger.info(
            "LangGraph checkpointer initialized with shared memory database"
        )

        return checkpointer


async def close_checkpointer() -> None:
    global _checkpointer

    async with _checkpointer_lock:
        _checkpointer = None