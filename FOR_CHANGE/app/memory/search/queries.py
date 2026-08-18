from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncidentSearchQuery(BaseModel):
    """Structured incident filters. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    number: str | None = None
    status: str | None = None
    priority_code: str | None = None
    system_name: str | None = None
    work_group: str | None = None
    element_name: str | None = None
    executor_name: str | None = None
    stand: str | None = None

    start_time_from: date | None = None
    start_time_to: date | None = None
    end_time_from: date | None = None
    end_time_to: date | None = None

    mttd_min: float | None = Field(default=None, ge=0)
    mttd_max: float | None = Field(default=None, ge=0)
    mttr_min: float | None = Field(default=None, ge=0)
    mttr_max: float | None = Field(default=None, ge=0)
    downtime_min: float | None = Field(default=None, ge=0)
    downtime_max: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> "IncidentSearchQuery":
        _check_date_range(
            "start_time",
            self.start_time_from,
            self.start_time_to,
        )
        _check_date_range(
            "end_time",
            self.end_time_from,
            self.end_time_to,
        )
        _check_numeric_range("mttd", self.mttd_min, self.mttd_max)
        _check_numeric_range("mttr", self.mttr_min, self.mttr_max)
        _check_numeric_range(
            "downtime",
            self.downtime_min,
            self.downtime_max,
        )
        return self

    def to_normalized_dict(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True, mode="json")


class AssignmentSearchQuery(BaseModel):
    """Structured assignment filters. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    incident_id: str | None = None
    ior: str | None = None
    unit: str | None = None
    responsible: str | None = None
    status: str | None = None

    deadline_from: date | None = None
    deadline_to: date | None = None
    assigned_at_from: date | None = None
    assigned_at_to: date | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "AssignmentSearchQuery":
        _check_date_range(
            "deadline",
            self.deadline_from,
            self.deadline_to,
        )
        _check_date_range(
            "assigned_at",
            self.assigned_at_from,
            self.assigned_at_to,
        )
        return self

    def to_normalized_dict(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True, mode="json")


SearchQuery = IncidentSearchQuery | AssignmentSearchQuery
EntityType = Literal["incidents", "assignments"]


def _check_date_range(
    field_name: str,
    lower: date | None,
    upper: date | None,
) -> None:
    if lower is None or upper is None:
        return

    if lower > upper:
        raise ValueError(
            f"{field_name}_from must not be greater than {field_name}_to"
        )


def _check_numeric_range(
    field_name: str,
    minimum: float | None,
    maximum: float | None,
) -> None:
    if minimum is None or maximum is None:
        return

    if minimum > maximum:
        raise ValueError(
            f"{field_name}_min must not be greater than {field_name}_max"
        )