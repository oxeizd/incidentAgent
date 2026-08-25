from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


EntityCatalogType = Literal[
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
]

EntityLookupStatus = Literal["matched", "ambiguous", "not_found"]

ENTITY_CATALOG_TYPES: tuple[EntityCatalogType, ...] = (
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
)

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_catalog_value(value: str) -> str:
    """
    Единственная нормализация для записи и поиска в entity catalog.

    Нормализация намеренно не делает транслитерацию: она используется
    только внутри fuzzy score, чтобы канонический DB key оставался
    предсказуемым и читаемым.
    """
    if not isinstance(value, str):
        raise TypeError("Catalog value must be a string")

    normalized = value.casefold().replace("ё", "е").strip()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()

    return normalized


class EntityCatalogUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityCatalogType
    canonical_value: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    source_count: int = Field(default=0, ge=0)

    @field_validator("canonical_value", mode="before")
    @classmethod
    def normalize_canonical_value(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("canonical_value must be a string")

        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("canonical_value must not be blank")

        return normalized

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> list[str]:
        if value is None:
            return []

        if not isinstance(value, list):
            raise ValueError("aliases must be a list of strings")

        result: list[str] = []
        seen_normalized: set[str] = set()

        for item in value:
            if not isinstance(item, str):
                raise ValueError("aliases must contain only strings")

            alias = " ".join(item.strip().split())
            if not alias:
                continue

            normalized_alias = normalize_catalog_value(alias)
            if not normalized_alias or normalized_alias in seen_normalized:
                continue

            seen_normalized.add(normalized_alias)
            result.append(alias)

        return result


class EntityCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    entity_type: EntityCatalogType
    canonical_value: str = Field(min_length=1, max_length=500)
    normalized_value: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    source_count: int = Field(ge=0)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class EntityLookupCandidate(BaseModel):
    """
    Один вариант, который может быть показан пользователю или использован
    агентом для уточняющего вопроса.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    entity_type: EntityCatalogType
    value: str = Field(min_length=1, max_length=500)
    score: float = Field(ge=0.0, le=100.0)

    matched_by: Literal["canonical", "alias"]
    matched_value: str = Field(min_length=1, max_length=500)


class EntityLookupResult(BaseModel):
    """
    Стабильный контракт для tool calling.

    matched:
        match содержит единственную сущность, которую можно применять
        как фильтр.

    ambiguous:
        агент не выбирает. Он показывает candidates пользователю и
        ожидает явного уточнения.

    not_found:
        match отсутствует; candidates являются только подсказками.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    status: EntityLookupStatus

    entity_type: EntityCatalogType | None = None
    match: EntityLookupCandidate | None = None
    candidates: list[EntityLookupCandidate] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("query must be a string")

        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("query must not be blank")

        return normalized