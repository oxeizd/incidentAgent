from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EntityType = Literal["incidents", "assignments"]
SearchResultStatus = Literal["building", "ready", "failed"]
ArtifactType = Literal["memory.search_result_ref"]
DisplayFormat = Literal["text", "date", "datetime", "number"]


class DisplayColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    format: DisplayFormat = "text"
    truncate: int | None = Field(default=None, ge=1, le=10_000)


class DisplaySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    columns: list[DisplayColumn] = Field(min_length=1, max_length=50)


class PreviewRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    values: dict[str, str]


class SearchPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[PreviewRow] = Field(default_factory=list, max_length=20)


class SearchHit(BaseModel):
    """Full domain row selected by a search engine."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    payload: dict[str, Any]
    score: float | None = None


class SearchExecution(BaseModel):
    """Ordered full search output before snapshot persistence."""

    model_config = ConfigDict(extra="forbid")

    entity: EntityType
    normalized_query: dict[str, Any]
    hits: list[SearchHit]


class SearchResultReferenceArtifact(BaseModel):
    """Small artifact persisted with an assistant message."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: ArtifactType = "memory.search_result_ref"
    artifact_version: int = Field(default=1, ge=1)

    result_id: str = Field(min_length=1)
    entity: EntityType

    total_count: int = Field(ge=0)
    preview_count: int = Field(ge=0)

    display: DisplaySchema
    preview: SearchPreview

    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_preview_count(self) -> "SearchResultReferenceArtifact":
        if self.preview_count != len(self.preview.rows):
            raise ValueError("preview_count must equal len(preview.rows)")
        if self.preview_count > self.total_count:
            raise ValueError("preview_count must not exceed total_count")
        return self


class SearchResultRecord(BaseModel):
    """Stored metadata for a full search-result snapshot."""

    model_config = ConfigDict(extra="forbid")

    id: str
    owner_user_id: str
    source_thread_id: str | None

    entity: EntityType
    query: dict[str, Any]
    display: DisplaySchema

    total_count: int = Field(ge=0)
    status: SearchResultStatus
    artifact_version: int = Field(ge=1)

    created_at: datetime
    expires_at: datetime
    invalidated_at: datetime | None = None


class SearchResultItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    entity_id: str = Field(min_length=1)
    score: float | None = None


class ResultCursorPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SearchResultItemRef]
    next_cursor: str | None
    has_more: bool