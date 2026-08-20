from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.conversation import (
    IncidentRef,
    IncidentReportRef,
)


PresentationSourceKind = Literal[
    "incident",
    "report",
    "description",
]

PresentationStage = Literal[
    "collect",
    "preview",
    "save",
]

PresentationDetailLevel = Literal[
    "brief",
    "standard",
    "detailed",
]


class PresentationSource(BaseModel):
    """
    Источник данных presentation workflow.

    `loaded_data` — ограниченный snapshot из MemoryFacade/artifact sections.
    Он живёт только в активной/отложенной task и не должен содержать HTML,
    runtime objects или неограниченный исходный payload.
    """

    model_config = ConfigDict(extra="forbid")

    kind: PresentationSourceKind
    incident_ref: IncidentRef | None = None
    report_ref: IncidentReportRef | None = None
    raw_description: str | None = None

    loaded_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "PresentationSource":
        if self.kind == "incident" and self.incident_ref is None:
            raise ValueError(
                "incident source requires incident_ref"
            )

        if self.kind == "report" and self.report_ref is None:
            raise ValueError(
                "report source requires report_ref"
            )

        if self.kind == "description" and not self.raw_description:
            raise ValueError(
                "description source requires raw_description"
            )

        return self


class PresentationCollectionDecision(BaseModel):
    """
    LLM-решение о достаточности данных для PresentationDocument.

    ready:
    - fields содержит готовый полный PresentationDocument JSON payload;
    - missing_fields/question запрещены.

    clarify:
    - missing_fields + question обязательны;
    - fields содержит известные частично заполненные document fields, чтобы
      следующий ответ пользователя дополнял уже собранное, а не затирал его.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["ready", "clarify"]

    fields: dict[str, Any] = Field(default_factory=dict)

    missing_fields: list[str] = Field(default_factory=list)
    question: str | None = None

    detail_level: PresentationDetailLevel = "standard"

    @model_validator(mode="after")
    def validate_shape(
        self,
    ) -> "PresentationCollectionDecision":
        if self.action == "ready":
            if self.missing_fields or self.question is not None:
                raise ValueError(
                    "ready collection must not contain "
                    "missing_fields/question"
                )

        if self.action == "clarify":
            if not self.missing_fields:
                raise ValueError(
                    "clarify requires missing_fields"
                )

            if not self.question:
                raise ValueError(
                    "clarify requires question"
                )

        return self


class PresentationAnswerUpdate(BaseModel):
    """
    LLM extraction результата обычного текстового ответа пользователя.

    `fields_update` содержит только поля, реально подтверждённые ответом.
    Никаких дефолтов «—» здесь нет: отсутствие поля означает, что оно не
    было получено из последней реплики.
    """

    model_config = ConfigDict(extra="forbid")

    fields_update: dict[str, Any] = Field(
        default_factory=dict
    )


class PresentationPreviewDecision(BaseModel):
    """
    Форматирует короткое user-facing summary будущей presentation.

    Не сохраняет документ. PresentationDocument валидируется Python-кодом
    до формирования preview.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=3_000)
    key_points: list[str] = Field(
        default_factory=list,
        max_length=10,
    )