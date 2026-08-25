from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EntityType = Literal["incidents", "assignments"]
SearchResultStatus = Literal["building", "ready", "failed"]
ArtifactType = Literal["memory.search_result_ref"]
DisplayFormat = Literal["text", "date", "datetime", "number"]


class DisplayColumn(BaseModel):
    """One explicitly allowed UI field in a search display profile."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    format: DisplayFormat = "text"
    truncate: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
    )


class DisplaySchema(BaseModel):
    """
    Stable display schema saved together with search-result snapshot.

    Schema is part of the persisted artifact contract: UI can render an
    old search result even when a future profile definition changes.
    """

    model_config = ConfigDict(extra="forbid")

    profile: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    columns: list[DisplayColumn] = Field(
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_unique_column_keys(self) -> "DisplaySchema":
        keys = [column.key for column in self.columns]

        if len(keys) != len(set(keys)):
            raise ValueError(
                "Display schema column keys must be unique"
            )

        return self


class PreviewRow(BaseModel):
    """UI-safe projection of one domain row."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=500)
    values: dict[str, str] = Field(default_factory=dict)


class SearchPreview(BaseModel):
    """Bounded user-facing preview embedded in assistant response."""

    model_config = ConfigDict(extra="forbid")

    rows: list[PreviewRow] = Field(
        default_factory=list,
        max_length=20,
    )


class SearchHit(BaseModel):
    """
    One full domain row selected by a query engine.

    This is internal runtime data. It is not persisted in search-result
    snapshots and must not be sent to UI unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None


class SearchExecution(BaseModel):
    """Ordered query-engine output before snapshot persistence."""

    model_config = ConfigDict(extra="forbid")

    entity: EntityType
    normalized_query: dict[str, Any] = Field(default_factory=dict)
    hits: list[SearchHit] = Field(default_factory=list)


class SearchResultReferenceArtifact(BaseModel):
    """Compact immutable search-result reference stored with a message."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: ArtifactType = "memory.search_result_ref"
    artifact_version: int = Field(default=1, ge=1)

    result_id: str = Field(min_length=1, max_length=200)
    entity: EntityType

    total_count: int = Field(ge=0)
    preview_count: int = Field(ge=0)

    display: DisplaySchema
    preview: SearchPreview

    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_preview(self) -> "SearchResultReferenceArtifact":
        if self.preview_count != len(self.preview.rows):
            raise ValueError(
                "preview_count must equal len(preview.rows)"
            )

        if self.preview_count > self.total_count:
            raise ValueError(
                "preview_count must not exceed total_count"
            )

        if self.expires_at <= self.created_at:
            raise ValueError(
                "expires_at must be later than created_at"
            )

        return self


class SearchResultRecord(BaseModel):
    """Stored metadata for one immutable search-result snapshot."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    source_thread_id: str | None = Field(
        default=None,
        max_length=200,
    )

    entity: EntityType
    query: dict[str, Any] = Field(default_factory=dict)
    display: DisplaySchema

    total_count: int = Field(ge=0)
    status: SearchResultStatus
    artifact_version: int = Field(ge=1)

    created_at: datetime
    expires_at: datetime
    invalidated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "SearchResultRecord":
        if self.expires_at <= self.created_at:
            raise ValueError(
                "expires_at must be later than created_at"
            )

        if (
            self.invalidated_at is not None
            and self.invalidated_at < self.created_at
        ):
            raise ValueError(
                "invalidated_at must not precede created_at"
            )

        return self


class SearchResultItemRef(BaseModel):
    """One ordered entity reference stored in a search-result snapshot."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    entity_id: str = Field(min_length=1, max_length=500)
    score: float | None = None


class ResultCursorPage(BaseModel):
    """One ordered page of persisted result item references."""

    model_config = ConfigDict(extra="forbid")

    items: list[SearchResultItemRef] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def validate_cursor_state(self) -> "ResultCursorPage":
        if self.has_more and not self.next_cursor:
            raise ValueError(
                "next_cursor is required when has_more is true"
            )

        if not self.has_more and self.next_cursor is not None:
            raise ValueError(
                "next_cursor must be null when has_more is false"
            )

        return self