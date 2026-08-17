from __future__ import annotations

import asyncio
import logging

from memory.artifacts.search_results.repository import SearchResultRepository

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SECONDS = 3600  # раз в час


async def run_search_result_cleanup_once(
    repository: SearchResultRepository,
) -> int:
    deleted = await repository.cleanup_expired()

    if deleted:
        logger.info("Cleaned up %d expired search result(s)", deleted)

    return deleted


async def run_search_result_cleanup_forever(
    repository: SearchResultRepository,
    *,
    interval_seconds: int = _CLEANUP_INTERVAL_SECONDS,
) -> None:
    """Long-running loop; schedule as an app-lifespan background task."""
    while True:
        try:
            await run_search_result_cleanup_once(repository)
        except Exception:
            logger.exception("Search result cleanup failed")

        await asyncio.sleep(interval_seconds)