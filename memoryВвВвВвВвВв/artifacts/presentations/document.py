from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DASH = "—"

AssignmentPriority = Literal["high", "medium", "low"]
AssignmentStatus = Literal[
    "new",
    "in_progress",
    "done",
    "cancelled",
]
AlertStatus = Literal["worked", "failed"]


class PresentationExtraField(BaseModel):
    """Произвольное поле, добавленное пользователем в карточку инцидента."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: str = DASH

    @field_validator("label", "value", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            return DASH

        normalized = value.strip()
        return normalized or DASH


class PresentationAlert(BaseModel):
    """Один алерт, показанный в карточке инцидента."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    status: AlertStatus

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("alert text must not be blank")

        return normalized


class PresentationAlerts(BaseModel):
    """Группы алертов одного инцидента."""

    model_config = ConfigDict(extra="forbid")

    worked: list[PresentationAlert] = Field(default_factory=list)
    failed: list[PresentationAlert] = Field(default_factory=list)


class PresentationIncident(BaseModel):
    """
    Один инцидент / ИОР в презентации.

    Презентация хранит incidents[] в JSON и не обязана ссылаться на
    таблицу incidents: пользователь может создавать и редактировать
    несколько карточек вручную.
    """

    model_config = ConfigDict(extra="forbid")

    number: str = DASH
    unit: str = DASH
    team: str = DASH
    brief: str = DASH
    description: str = DASH
    cause: str = DASH
    chain: str = DASH
    stage: str = DASH
    impact: str = DASH
    losses: str = DASH
    operational_measures: str = DASH
    systemic_measures: str = DASH

    timeline: list[str] = Field(default_factory=list)
    alerts: PresentationAlerts = Field(
        default_factory=PresentationAlerts,
    )
    extra_fields: list[PresentationExtraField] = Field(
        default_factory=list,
    )

    @field_validator(
        "number",
        "unit",
        "team",
        "brief",
        "description",
        "cause",
        "chain",
        "stage",
        "impact",
        "losses",
        "operational_measures",
        "systemic_measures",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            return DASH

        normalized = value.strip()
        return normalized or DASH

    @field_validator("timeline", mode="before")
    @classmethod
    def normalize_timeline(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []

        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]


class PresentationAssignment(BaseModel):
    """Одно поручение, общее для презентации."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    title: str = DASH
    description: str = DASH
    addresses: str = DASH

    priority: AssignmentPriority | None = None
    type: str | None = None

    status: AssignmentStatus | None = None
    created_at: str | None = None
    deadline_at: str | None = None
    responsible: str | None = None

    expected_result: str = DASH
    result: str | None = None

    @field_validator(
        "title",
        "description",
        "addresses",
        "expected_result",
        mode="before",
    )
    @classmethod
    def normalize_display_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            return DASH

        normalized = value.strip()
        return normalized or DASH

    @field_validator(
        "id",
        "type",
        "created_at",
        "deadline_at",
        "responsible",
        "result",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None

        normalized = value.strip()
        return normalized or None


class PresentationDocument(BaseModel):
    """
    Единственный persisted payload презентации.

    schema_version=2:
    - incidents содержит одну или больше карточек ИОР;
    - assignments остаётся общим списком поручений презентации;
    - analysis_markdown — общий RCA/комиссионный анализ.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2

    incidents: list[PresentationIncident] = Field(
        default_factory=list,
    )
    analysis_markdown: str = ""
    assignments: list[PresentationAssignment] = Field(
        default_factory=list,
    )

    @field_validator("analysis_markdown", mode="before")
    @classmethod
    def normalize_analysis(cls, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def to_storage(self) -> dict[str, Any]:
        """Возвращает JSON-совместимый документ для persistence."""
        return self.model_dump(mode="json")