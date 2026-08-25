from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssignmentUpsert(BaseModel):
    """Validated canonical payload for one independent assignment artifact."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=200)

    incident_id: str | None = Field(default=None, max_length=200)
    ior: str | None = Field(default=None, max_length=200)

    task: str | None = Field(default=None, max_length=10_000)
    unit: str | None = Field(default=None, max_length=500)
    assignment: str = Field(min_length=1, max_length=50_000)
    responsible: str | None = Field(default=None, max_length=500)

    deadline: datetime | str | None = None
    assigned_at: datetime | None = None
    status: str | None = Field(default=None, max_length=200)

    source_payload: dict[str, Any] | None = None

    @field_validator(
        "id",
        "incident_id",
        "ior",
        "task",
        "unit",
        "responsible",
        "status",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None

    @field_validator("assignment", mode="before")
    @classmethod
    def normalize_required_assignment(cls, value: Any) -> str:
        if value is None:
            raise ValueError("assignment is required")

        normalized = str(value).strip()
        if not normalized:
            raise ValueError("assignment must not be blank")

        return normalized