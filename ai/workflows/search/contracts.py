from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SearchEntity = Literal[
    "incidents",
    "assignments",
]

SearchPlanMode = Literal[
    "records",
    "analytics",
    "clarify",
]

SortOrder = Literal[
    "asc",
    "desc",
]

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

SemanticRankingMode = Literal[
    "primary",
    "candidate_filter",
]

CatalogEntityType = Literal[
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
]


INCIDENT_FILTERS = frozenset(
    {
        "number",
        "status",
        "priority_code",
        "system_name",
        "work_group",
        "element_name",
        "executor_name",
        "stand",
        "start_time_from",
        "start_time_to",
        "end_time_from",
        "end_time_to",
        "mttd_min",
        "mttd_max",
        "mttr_min",
        "mttr_max",
        "downtime_min",
        "downtime_max",
    }
)

ASSIGNMENT_FILTERS = frozenset(
    {
        "id",
        "incident_id",
        "ior",
        "unit",
        "responsible",
        "created_by",
        "status",
        "deadline_from",
        "deadline_to",
        "assigned_at_from",
        "assigned_at_to",
    }
)

FORBIDDEN_TEXT_FILTERS = frozenset(
    {
        "text",
        "text_error",
        "error",
        "description",
        "message",
        "query",
        "symptom",
        "title",
        "details",
    }
)


class SearchSort(BaseModel):
    """Один разрешённый business sorting criterion."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=100)
    order: SortOrder = "desc"


class SemanticSearchSpec(BaseModel):
    """Семантическая часть record search без свободного текста в filters."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=10_000)
    ranking: SemanticRankingMode = "primary"
    candidate_limit: int = Field(default=200, ge=1, le=500)


class RecordSearchPlan(BaseModel):
    """Строгий план для MemoryFacade.search(). SQL здесь не допускается."""

    model_config = ConfigDict(extra="forbid")

    entity: SearchEntity
    filters: dict[str, Any] = Field(default_factory=dict)
    semantic: SemanticSearchSpec | None = None
    sorts: list[SearchSort] = Field(default_factory=list, max_length=5)
    top_n: int | None = Field(default=None, ge=1, le=200)

    @model_validator(mode="after")
    def validate_allowed_fields(self) -> "RecordSearchPlan":
        if not self.filters and self.semantic is None:
            raise ValueError("record plan requires filters or semantic")

        forbidden = set(self.filters) & FORBIDDEN_TEXT_FILTERS
        if forbidden:
            raise ValueError(
                "free-text filters are forbidden; use semantic.query: "
                f"{sorted(forbidden)}"
            )

        allowed_filters = (
            INCIDENT_FILTERS
            if self.entity == "incidents"
            else ASSIGNMENT_FILTERS
        )
        unknown_filters = set(self.filters) - allowed_filters
        if unknown_filters:
            raise ValueError(
                f"unsupported filters for {self.entity}: "
                f"{sorted(unknown_filters)}"
            )

        allowed_sorts = (
            set(IncidentSortField.__args__)
            if self.entity == "incidents"
            else set(AssignmentSortField.__args__)
        )
        unknown_sorts = {
            sort.field
            for sort in self.sorts
            if sort.field not in allowed_sorts
        }
        if unknown_sorts:
            raise ValueError(
                f"unsupported sort fields for {self.entity}: "
                f"{sorted(unknown_sorts)}"
            )

        if (
            self.semantic is not None
            and self.semantic.ranking == "candidate_filter"
            and not self.sorts
        ):
            raise ValueError(
                "candidate_filter semantic requires sorts"
            )

        return self


class AnalyticsSearchPlan(BaseModel):
    """
    План для MemoryFacade.query_analytics_sql().

    Memory layer остаётся окончательным security boundary: он выполняет
    собственную SQL-валидацию и preflight compilation.
    """

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=20_000)
    parameters: list[str | int | float | bool | None] = Field(
        default_factory=list,
        max_length=100,
    )
    max_rows: int = Field(default=100, ge=1, le=500)


class SearchPlan(BaseModel):
    """
    Структурированный результат внутреннего Search Agent.

    records   -> MemoryFacade.search()
    analytics -> MemoryFacade.query_analytics_sql()
    clarify   -> search step ждёт обычный текстовый ответ пользователя.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SearchPlanMode
    records: RecordSearchPlan | None = None
    analytics: AnalyticsSearchPlan | None = None
    question: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
    )

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "SearchPlan":
        if self.mode == "records":
            if self.records is None:
                raise ValueError("records mode requires records")
            if self.analytics is not None or self.question is not None:
                raise ValueError(
                    "records mode must contain only records"
                )
            return self

        if self.mode == "analytics":
            if self.analytics is None:
                raise ValueError("analytics mode requires analytics")
            if self.records is not None or self.question is not None:
                raise ValueError(
                    "analytics mode must contain only analytics"
                )
            return self

        if self.mode == "clarify":
            if not self.question:
                raise ValueError("clarify mode requires question")
            if self.records is not None or self.analytics is not None:
                raise ValueError(
                    "clarify mode must not contain plans"
                )
            return self

        raise ValueError(f"unsupported search mode: {self.mode!r}")