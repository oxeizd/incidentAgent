from __future__ import annotations

from app.memory.db.connection import Database
from app.memory.search.structured import (
    INCIDENT_SEARCH_CONFIG,
    SearchSort,
    StructuredSearchService,
)


class IncidentSearch(StructuredSearchService):
    """
    Structured incident search.

    Класс оставлен как compatibility boundary для существующих вызовов;
    реализация находится в generic StructuredSearchService.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database, config=INCIDENT_SEARCH_CONFIG)


__all__ = ["IncidentSearch", "SearchSort"]