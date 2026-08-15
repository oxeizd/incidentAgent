import logging
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.memory.db.database import get_db

logger = logging.getLogger(__name__)
_checkpointer: AsyncSqliteSaver | None = None


async def get_checkpointer() -> AsyncSqliteSaver:
    global _checkpointer
    if _checkpointer is None:
        conn = await get_db()
        _checkpointer = AsyncSqliteSaver(conn)
        await _checkpointer.setup()
        logger.info("LangGraph checkpointer initialised (shared DB connection)")
    return _checkpointer


async def close_checkpointer() -> None:
    """ИСПРАВЛЕНО: раньше закрывал _checkpointer.conn напрямую — тот же
    объект, что и database._db. Теперь только отпускает ссылку; физическое
    соединение закрывает исключительно database.close_db()."""
    global _checkpointer
    _checkpointer = None
    logger.info("Checkpointer reference released (connection lifecycle owned by database.py)")