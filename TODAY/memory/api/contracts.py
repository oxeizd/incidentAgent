from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from memory.artifacts.presentations.contracts import PresentationRecord
from memory.artifacts.presentations.document import PresentationDocument

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
    limit: int | None = Field(default=None, ge=1, le=1000)
    preview_limit: int | None = Field(default=None, ge=1, le=20)


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)


class ThreadSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    created_at: str
    updated_at: str
    last_message_preview: str | None = None
    last_message_role: str | None = None


class ThreadPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ThreadSummary] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class ThreadMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    artifact: dict[str, Any] | None = None
    created_at: str


class ThreadMessagesPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ThreadMessage] = Field(default_factory=list)
    next_before: str | None = None
    has_more: bool = False


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


class CreatePresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=200)
    fields: PresentationDocument = Field(
        default_factory=PresentationDocument
    )


class UpdatePresentationFieldsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: PresentationDocument


class CreatePresentationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presentation_id: str


class PresentationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presentations: list[PresentationRecord]