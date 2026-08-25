from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


EntityCatalogType = Literal[
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
]

ENTITY_CATALOG_TYPES: tuple[EntityCatalogType, ...] = (
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
)

_WHITESPACE_RE = re.compile(r"\s+")


class EntityCatalogEntry(BaseModel):
    """Одна каноническая entity из persistent DB catalog."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    entity_type: EntityCatalogType
    canonical_value: str = Field(min_length=1, max_length=500)
    normalized_value: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str


class EntityCatalogUpsert(BaseModel):
    """Validated request to create or update a catalog entry."""

    model_config = ConfigDict(extra="forbid")

    entity_type: EntityCatalogType
    canonical_value: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)

    @field_validator("canonical_value")
    @classmethod
    def normalize_canonical_value(cls, value: str) -> str:
        normalized = _normalize_display_text(value)

        if not normalized:
            raise ValueError("canonical_value must not be blank")

        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized_aliases: list[str] = []

        for value in values:
            if not isinstance(value, str):
                continue

            alias = _normalize_display_text(value)
            key = alias.casefold()

            if not alias or key in seen:
                continue

            seen.add(key)
            normalized_aliases.append(alias)

        return normalized_aliases


def normalize_catalog_value(value: str) -> str:
    """
    Stable normalized key for exact matching and cache keys.

    Fuzzy/token scoring intentionally belongs to catalog resolver, not
    to DB contracts or persistence logic.
    """
    return _normalize_display_text(value).casefold()


def _normalize_display_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    return _WHITESPACE_RE.sub(" ", value.strip())