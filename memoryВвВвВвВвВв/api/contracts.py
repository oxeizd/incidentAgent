from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.memory.artifacts.presentations.contracts import (
    PresentationRecord,
)
from app.memory.artifacts.presentations.document import (
    PresentationDocument,
)
from app.memory.artifacts.imports.contracts import (
    ImportEntity,
    ImportErrorItem,
    ImportStatus,
)
from app.memory.search.contracts import (
    EntityType,
    SearchResultReferenceArtifact,
)


class SearchRequest(BaseModel):
    """
    Один API contract для structured, semantic и hybrid search.

    Хотя HTTP сохраняет отдельные routes для incidents и assignments,
    entity определяется route, а запрос всегда проходит через один
    MemoryFacade.search().
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=200)

    filters: dict[str, Any] = Field(default_factory=dict)
    semantic_query: str | None = Field(
        default=None,
        max_length=10_000,
    )

    sorts: list[dict[str, str]] = Field(default_factory=list)
    top_n: int | None = Field(default=None, ge=1, le=500)
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
    role: str
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

    entity: ImportEntity
    status: ImportStatus

    total_items: int
    imported_count: int
    failed_count: int

    errors: list[ImportErrorItem] = Field(default_factory=list)
    warnings: list[ImportErrorItem] = Field(default_factory=list)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: SearchResultReferenceArtifact


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

    presentations: list[PresentationRecord] = Field(
        default_factory=list
    )