from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.memory.artifacts.catalog.contracts import (
    EntityCatalogEntry,
    EntityCatalogType,
    EntityCatalogUpsert,
    normalize_catalog_value,
)
from app.memory.artifacts.catalog.repository import EntityCatalogRepository
from app.memory.utils import compact_text, optional_text


class EntityCatalogService:
    """
    Application service for a persistent entity catalog.

    Manual upsert can provide aliases. replace_values is used only for a
    complete rebuild from current incident data for one entity type.
    """

    def __init__(
        self,
        *,
        repository: EntityCatalogRepository,
    ) -> None:
        self._repository = repository

    async def upsert(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
        aliases: list[str] | None = None,
        source_count: int = 0,
    ) -> EntityCatalogEntry:
        entry = EntityCatalogUpsert(
            entity_type=entity_type,
            canonical_value=canonical_value,
            aliases=aliases or [],
            source_count=source_count,
        )

        return await self._repository.upsert(
            entry=entry,
            normalized_value=normalize_catalog_value(
                entry.canonical_value
            ),
        )

    async def replace_values(
        self,
        *,
        entity_type: EntityCatalogType,
        values: Iterable[object],
    ) -> int:
        """
        Replaces one catalog entity type from its complete source dataset.

        The caller must pass all current source values, not an import batch.
        This guarantees removal of stale names and correct source_count.
        """
        counts = Counter(
            value
            for raw_value in values
            if (value := _source_value(raw_value)) is not None
        )

        entries = [
            (
                EntityCatalogUpsert(
                    entity_type=entity_type,
                    canonical_value=value,
                    source_count=count,
                ),
                normalize_catalog_value(value),
            )
            for value, count in sorted(
                counts.items(),
                key=lambda item: item[0].casefold(),
            )
        ]

        return await self._repository.replace_type(
            entity_type=entity_type,
            entries=entries,
        )

    async def get(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> EntityCatalogEntry | None:
        normalized = _source_value(canonical_value)

        if normalized is None:
            return None

        return await self._repository.get(
            entity_type=entity_type,
            canonical_value=normalized,
        )

    async def list_by_type(
        self,
        *,
        entity_type: EntityCatalogType,
        limit: int | None = None,
    ) -> list[EntityCatalogEntry]:
        return await self._repository.list_by_type(
            entity_type=entity_type,
            limit=limit,
        )

    async def list_all(self) -> list[EntityCatalogEntry]:
        return await self._repository.list_all()

    async def delete(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> bool:
        normalized = _source_value(canonical_value)

        if normalized is None:
            return False

        return await self._repository.delete(
            entity_type=entity_type,
            canonical_value=normalized,
        )

    async def count(self) -> int:
        return await self._repository.count()


def _source_value(value: object) -> str | None:
    text = optional_text(value)

    if text is None:
        return None

    return compact_text(text)