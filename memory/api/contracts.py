from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EntityType = Literal["incidents", "assignments"]


class StructuredSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)
    preview_limit: int | None = Field(default=None, ge=1, le=20)


class SemanticSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=200)
    query_text: str = Field(min_length=1, max_length=10_000)
    limit: int | None = Field(default=None, ge=1, le=1_000)
    preview_limit: int | None = Field(default=None, ge=1, le=20)


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)


class ImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: EntityType
    total_items: int
    imported_count: int
    failed_count: int
    errors: list[dict[str, Any]]


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str


class BackfillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incidents_processed: int | None = None
    assignments_processed: int | None = None