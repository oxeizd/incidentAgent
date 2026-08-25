from __future__ import annotations

from app.memory.db.connection import Database
from app.memory.search.structured import (
    ASSIGNMENT_SEARCH_CONFIG,
    SearchSort,
    StructuredSearchService,
)


class AssignmentSearch(StructuredSearchService):
    """
    Structured assignment search.

    Класс оставлен как compatibility boundary для существующих вызовов;
    реализация находится в generic StructuredSearchService.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database, config=ASSIGNMENT_SEARCH_CONFIG)


__all__ = ["AssignmentSearch", "SearchSort"]