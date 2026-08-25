from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.memory.catalog.contracts import (
    EntityCatalogEntry,
    EntityCatalogType,
    EntityCatalogUpsert,
    normalize_catalog_value,
)
from app.memory.catalog.repository import EntityCatalogRepository


class EntityCatalogService:
    """
    Application service persistent entity catalog.

    БД — authoritative source of truth. Resolver может получать полный
    snapshot через list_all() и держать TTL-cache вне memory-layer.
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

    async def refresh_values(
        self,
        *,
        entity_type: EntityCatalogType,
        values: Iterable[object],
    ) -> int:
        """
        Upserts source values and updates source_count for supplied values.

        Не удаляет отсутствующие значения: aliases могут быть созданы
        вручную, а каталог не должен стираться из-за частичного import.
        """
        counts = Counter(
            normalized
            for value in values
            if (
                normalized := _normalize_source_value(value)
            ) is not None
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
            for value, count in counts.items()
        ]

        return await self._repository.upsert_many(entries=entries)

    async def get(
        self,
        *,
        entity_type: EntityCatalogType,
        canonical_value: str,
    ) -> EntityCatalogEntry | None:
        return await self._repository.get(
            entity_type=entity_type,
            canonical_value=canonical_value.strip(),
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
        normalized = canonical_value.strip()
        if not normalized:
            return False

        return await self._repository.delete(
            entity_type=entity_type,
            canonical_value=normalized,
        )

    async def count(self) -> int:
        return await self._repository.count()


def _normalize_source_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = " ".join(value.strip().split())
    return normalized or None