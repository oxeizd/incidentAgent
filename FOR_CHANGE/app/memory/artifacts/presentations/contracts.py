from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory.artifacts.presentations.document import PresentationDocument


PresentationStatus = Literal["draft", "published"]


class PresentationCreate(BaseModel):
    """Validated payload to create one draft presentation."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    fields: PresentationDocument = Field(
        default_factory=PresentationDocument
    )

    @field_validator("owner_user_id", "thread_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("value must not be blank")

        return normalized


class PresentationFieldsUpdate(BaseModel):
    """Validated full replacement for an editable draft document."""

    model_config = ConfigDict(extra="forbid")

    fields: PresentationDocument


class PresentationRecord(BaseModel):
    """One stored draft or published presentation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    owner_user_id: str
    thread_id: str
    status: PresentationStatus

    fields: PresentationDocument
    published_snapshot: PresentationDocument | None = None

    created_at: str
    updated_at: str
    published_at: str | None = None