from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EntityName = Literal["incidents", "assignments"]
DisplayFormat = Literal["text", "date", "datetime"]


INCIDENT_FIELDS = frozenset({
    "number", "start_time", "end_time", "status", "priority_code",
    "resolution_code", "registration_basis", "inc_type", "stand",
    "system_name", "work_group", "element_name", "created_by",
    "executor_name", "reason_inc", "mttd", "mttr", "downtime",
})

ASSIGNMENT_FIELDS = frozenset({
    "id", "incident_id", "task", "unit", "assignment", "deadline",
    "date", "ior", "responsible",
})


class DisplayField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    format: DisplayFormat = "text"
    truncate: int | None = Field(default=None, ge=1, le=10_000)
    required: bool = False


class ChatOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    fields: list[DisplayField] = Field(min_length=1)


class EntityOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: ChatOutputConfig
    analysis: list[str] = Field(min_length=1)
    export: list[str] = Field(min_length=1)


class SearchOutputDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_size: int = Field(default=50, ge=1, le=200)
    max_page_size: int = Field(default=200, ge=1, le=500)
    preview_items: int = Field(default=10, ge=1, le=100)
    timestamp_format: str = "%d.%m.%Y %H:%M"
    date_format: str = "%d.%m.%Y"
    null_value: str = "—"

    @model_validator(mode="after")
    def check_page_limits(self) -> "SearchOutputDefaults":
        if self.page_size > self.max_page_size:
            raise ValueError("defaults.page_size must not exceed defaults.max_page_size")
        return self


class SearchOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    defaults: SearchOutputDefaults
    incidents: EntityOutputConfig
    assignments: EntityOutputConfig

    @field_validator("incidents")
    @classmethod
    def validate_incident_fields(cls, value: EntityOutputConfig) -> EntityOutputConfig:
        _validate_entity_fields("incidents", value, INCIDENT_FIELDS)
        return value

    @field_validator("assignments")
    @classmethod
    def validate_assignment_fields(cls, value: EntityOutputConfig) -> EntityOutputConfig:
        _validate_entity_fields("assignments", value, ASSIGNMENT_FIELDS)
        return value


def _validate_entity_fields(
    entity: EntityName,
    config: EntityOutputConfig,
    allowed_fields: frozenset[str],
) -> None:
    fields = [field.key for field in config.chat.fields] + config.analysis + config.export
    unknown = sorted(set(fields) - allowed_fields)
    if unknown:
        raise ValueError(f"Unknown {entity} output fields: {', '.join(unknown)}")

    duplicates = [
        field.key for field in config.chat.fields
        if sum(candidate.key == field.key for candidate in config.chat.fields) > 1
    ]
    if duplicates:
        raise ValueError(f"Duplicate {entity}.chat field keys: {', '.join(sorted(set(duplicates)))}")


class SearchDisplayColumn(BaseModel):
    key: str
    label: str


class SearchDisplay(BaseModel):
    title: str
    columns: list[SearchDisplayColumn]
    rows: list[dict[str, str]]


class SearchPage(BaseModel):
    """Stable tool artifact contract. total_count is authoritative; items is one page."""

    schema_version: int = 1
    entity: EntityName
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool
    applied_filters: dict[str, Any]
    items: list[dict[str, Any]]
    display: SearchDisplay

    @model_validator(mode="after")
    def check_consistency(self) -> "SearchPage":
        if self.returned_count != len(self.items):
            raise ValueError("returned_count must equal len(items)")
        if self.returned_count > self.limit:
            raise ValueError("returned_count must not exceed limit")
        if self.has_more != (self.offset + self.returned_count < self.total_count):
            raise ValueError("has_more is inconsistent with total_count/offset/returned_count")
        return self
