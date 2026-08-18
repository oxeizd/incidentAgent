from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Protocol

from memory.search.contracts import EntityType, SearchHit
from memory.search.queries import SearchQuery


class SupportsStreamingSearch(Protocol):
    async def stream(
        self,
        query: SearchQuery,
        *,
        batch_size: int,
    ) -> AsyncIterator[list[SearchHit]]: ...


def validate_batch_size(value: int) -> int:
    if value < 1:
        raise ValueError("batch_size must be at least 1")

    return min(value, 5_000)


def enumerate_hit_batch(
    hits: Iterable[SearchHit],
    *,
    start_position: int,
) -> list[tuple[int, SearchHit]]:
    return [
        (position, hit)
        for position, hit in enumerate(hits, start=start_position)
    ]