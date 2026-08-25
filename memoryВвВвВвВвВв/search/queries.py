from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SortOrder = Literal["asc", "desc"]

IncidentSortField = Literal[
    "start_time",
    "end_time",
    "mttd",
    "mttr",
    "downtime",
    "priority_code",
]

AssignmentSortField = Literal[
    "deadline",
    "assigned_at",
]

EntityType = Literal["incidents", "assignments"]


class SearchQueryBase(BaseModel):
    """Shared strict base for structured search filter schemas."""

    model_config = ConfigDict(extra="forbid")

    def to_normalized_dict(self) -> dict[str, object]:
        return self.model_dump(
            exclude_none=True,
            mode="json",
        )


class IncidentSearchQuery(SearchQueryBase):
    """Structured exact incident filters and date/numeric ranges."""

    number: str | None = None
    status: str | None = None
    priority_code: str | None = None
    system_name: str | None = None
    work_group: str | None = None
    element_name: str | None = None
    executor_name: str | None = None
    stand: str | None = None

    start_time_from: datetime | None = None
    start_time_to: datetime | None = None
    end_time_from: datetime | None = None
    end_time_to: datetime | None = None

    mttd_min: float | None = Field(default=None, ge=0)
    mttd_max: float | None = Field(default=None, ge=0)
    mttr_min: float | None = Field(default=None, ge=0)
    mttr_max: float | None = Field(default=None, ge=0)
    downtime_min: float | None = Field(default=None, ge=0)
    downtime_max: float | None = Field(default=None, ge=0)

    @field_validator(
        "number",
        "status",
        "priority_code",
        "system_name",
        "work_group",
        "element_name",
        "executor_name",
        "stand",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator(
        "mttd_min",
        "mttd_max",
        "mttr_min",
        "mttr_max",
        "downtime_min",
        "downtime_max",
    )
    @classmethod
    def validate_finite_number(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("numeric filter must be finite")
        return value

    @model_validator(mode="after")
    def validate_ranges(self) -> "IncidentSearchQuery":
        _check_datetime_range(
            "start_time",
            self.start_time_from,
            self.start_time_to,
        )
        _check_datetime_range(
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


class AssignmentSearchQuery(SearchQueryBase):
    """
    Structured exact assignment filters and date ranges.

    Input datetime values for assignment dates are reduced to their
    calendar date so LLM/API ISO datetime values do not fail strict
    Pydantic date parsing.
    """

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

    @field_validator(
        "id",
        "incident_id",
        "ior",
        "unit",
        "responsible",
        "status",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator(
        "deadline_from",
        "deadline_to",
        "assigned_at_from",
        "assigned_at_to",
        mode="before",
    )
    @classmethod
    def normalize_datetime_to_date(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, str):
            normalized = value.strip()

            if "T" in normalized:
                return normalized.split("T", 1)[0]

            return normalized

        return value

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


SearchQuery = IncidentSearchQuery | AssignmentSearchQuery


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("text filter must be a string")

    normalized = " ".join(value.split())
    return normalized or None


def _check_datetime_range(
    field_name: str,
    lower: datetime | None,
    upper: datetime | None,
) -> None:
    if lower is None or upper is None:
        return

    if lower.tzinfo is None and upper.tzinfo is not None:
        raise ValueError(
            f"{field_name}_from and {field_name}_to must both "
            "be timezone-aware or both be timezone-naive"
        )

    if lower.tzinfo is not None and upper.tzinfo is None:
        raise ValueError(
            f"{field_name}_from and {field_name}_to must both "
            "be timezone-aware or both be timezone-naive"
        )

    if lower > upper:
        raise ValueError(
            f"{field_name}_from must not be greater than "
            f"{field_name}_to"
        )


def _check_date_range(
    field_name: str,
    lower: date | None,
    upper: date | None,
) -> None:
    if lower is None or upper is None:
        return

    if lower > upper:
        raise ValueError(
            f"{field_name}_from must not be greater than "
            f"{field_name}_to"
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
            f"{field_name}_min must not be greater than "
            f"{field_name}_max"
        )