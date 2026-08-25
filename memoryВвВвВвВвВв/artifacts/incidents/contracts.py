from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IncidentUpsert(BaseModel):
    """
    Validated canonical payload for one incident.

    `resolution_description` preserves the source form text entered by an
    administrator. Parsed fields such as `reason_inc`, `solution`, `impact`,
    `start_time` and `end_time` are derived projections of that text.
    """

    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1, max_length=200)

    created_at: datetime | None = None
    target_date: datetime | None = None
    plan_finish_date: datetime | None = None
    close_date: datetime | None = None
    detection_time: datetime | None = None

    work_group: str | None = Field(default=None, max_length=500)
    element_name: str | None = Field(default=None, max_length=500)
    system_name: str | None = Field(default=None, max_length=500)
    created_by: str | None = Field(default=None, max_length=500)
    executor_name: str | None = Field(default=None, max_length=500)

    status: str | None = Field(default=None, max_length=200)
    priority_code: str | None = Field(default=None, max_length=100)
    resolution_code: str | None = Field(default=None, max_length=200)
    registration_basis: str | None = Field(default=None, max_length=500)
    inc_type: str | None = Field(default=None, max_length=200)
    stand: str | None = Field(default=None, max_length=200)

    description: str | None = Field(default=None, max_length=50_000)
    resolution_description: str | None = Field(default=None, max_length=50_000)
    reason_inc: str | None = Field(default=None, max_length=50_000)
    solution: str | None = Field(default=None, max_length=50_000)
    impact: str | None = Field(default=None, max_length=50_000)

    start_time: datetime | None = None
    end_time: datetime | None = None

    impact_custom_service: bool = False
    no_impact: bool = False
    is_root: bool = False

    mttd: float | None = Field(default=None, ge=0)
    mttr: float | None = Field(default=None, ge=0)
    downtime: float | None = Field(default=None, ge=0)

    month_created: int | None = Field(default=None, ge=1, le=12)
    quarter_created: int | None = Field(default=None, ge=1, le=4)

    ai_description: str | None = Field(default=None, max_length=50_000)

    @field_validator("number")
    @classmethod
    def strip_number(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("number must not be blank")
        return stripped

    @field_validator(
        "work_group",
        "element_name",
        "system_name",
        "created_by",
        "executor_name",
        "status",
        "priority_code",
        "resolution_code",
        "registration_basis",
        "inc_type",
        "stand",
        "description",
        "resolution_description",
        "reason_inc",
        "solution",
        "impact",
        "ai_description",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None