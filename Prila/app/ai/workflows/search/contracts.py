from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.conversation import (
    IncidentRef,
    SearchResultRef,
)


SearchEntity = Literal[
    "incidents",
    "assignments",
]

SearchMode = Literal[
    "structured",
    "semantic_similarity",
]

NormalizationAction = Literal[
    "execute",
    "select_candidate",
    "clarify",
]

CatalogEntityType = Literal[
    "system_name",
    "work_group",
    "executor_name",
    "element_name",
]

IncidentSelectionAction = Literal[
    "confirm_incident",
    "select_incident",
    "refine_search",
]


class CatalogCandidate(BaseModel):
    """
    Кандидат, реально возвращённый tool `lookup_entity`.

    Поля совпадают с фактическим контрактом `lookup_entities()`. Runtime не
    принимает никаких CatalogCandidate, не встретившихся в tool output —
    это единственная защита от того, что LLM придумает canonical значение.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    entity_type: CatalogEntityType
    name: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=100.0)


_INCIDENT_FILTERS = frozenset(
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

_ASSIGNMENT_FILTERS = frozenset(
    {
        "id",
        "incident_id",
        "ior",
        "unit",
        "responsible",
        "status",
        "deadline_from",
        "deadline_to",
        "assigned_at_from",
        "assigned_at_to",
    }
)


class SearchPlan(BaseModel):
    """
    Готовый план поиска после нормализации.

    `filters` должны использовать реальные canonical values (`name` из
    CatalogCandidate), а не сырые слова пользователя.
    """

    model_config = ConfigDict(extra="forbid")

    entity: SearchEntity
    mode: SearchMode

    filters: dict[str, Any] | None = None
    query_text: str | None = Field(
        default=None,
        min_length=2,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "SearchPlan":
        if self.mode == "structured":
            if not self.filters:
                raise ValueError(
                    "structured mode requires filters"
                )

            if self.query_text is not None:
                raise ValueError(
                    "structured mode must not contain query_text"
                )

            allowed_filters = (
                _INCIDENT_FILTERS
                if self.entity == "incidents"
                else _ASSIGNMENT_FILTERS
            )

            unknown_filters = (
                set(self.filters) - allowed_filters
            )

            if unknown_filters:
                raise ValueError(
                    "unsupported filters for "
                    f"{self.entity}: {sorted(unknown_filters)}"
                )

        elif self.mode == "semantic_similarity":
            if not self.query_text:
                raise ValueError(
                    "semantic mode requires query_text"
                )

            if self.filters is not None:
                raise ValueError(
                    "semantic mode must not contain filters"
                )

        return self


class SearchNormalizationDecision(BaseModel):
    """
    Финальное решение Search Normalizer.

    execute:
    - plan обязателен;
    - used_candidate_ids — реальные candidates, использованные в filters.

    select_candidate:
    - question + минимум два option_ids из реальных tool results;
    - выбор пользователя нужен для равнозначных сущностей.

    clarify:
    - question обязателен;
    - используется, когда не распознан термин, неясен период, конфликтуют
      filters или не хватает обязательной части запроса;
    - options отсутствуют: пользователь отвечает обычным текстом.
    """

    model_config = ConfigDict(extra="forbid")

    action: NormalizationAction

    plan: SearchPlan | None = None
    used_candidate_ids: list[str] = Field(default_factory=list)

    question: str | None = None
    option_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> "SearchNormalizationDecision":
        if self.action == "execute":
            if self.plan is None:
                raise ValueError(
                    "execute action requires plan"
                )

            if self.question is not None or self.option_ids:
                raise ValueError(
                    "execute cannot contain question or option_ids"
                )

        elif self.action == "select_candidate":
            if not self.question or not self.question.strip():
                raise ValueError(
                    "select_candidate requires question"
                )

            if len(self.option_ids) < 2:
                raise ValueError(
                    "select_candidate requires at least two option_ids"
                )

            if len(self.option_ids) != len(set(self.option_ids)):
                raise ValueError(
                    "option_ids must be unique"
                )

            if self.plan is not None:
                raise ValueError(
                    "select_candidate must not contain plan"
                )

            if self.used_candidate_ids:
                raise ValueError(
                    "select_candidate must not use candidates"
                )

        elif self.action == "clarify":
            if not self.question or not self.question.strip():
                raise ValueError(
                    "clarify requires question"
                )

            if (
                self.plan is not None
                or self.used_candidate_ids
                or self.option_ids
            ):
                raise ValueError(
                    "clarify may contain only question"
                )

        return self


    class SearchDependencyOutcome(BaseModel):
        """
        Результат Search как внутренней стадии другого workflow.

        `result_ref` сохраняет ссылку на immutable search snapshot.
        `selected_incident_ref` появляется только после выбора конкретного
        инцидента Search агентом/пользователем.

        RCA/Presentation не получают preview rows и не анализируют search output:
        их вход — только стабильный IncidentRef.
        """

        model_config = ConfigDict(extra="forbid")

        result_ref: SearchResultRef
        selected_incident_ref: IncidentRef


    class SearchIncidentCandidate(BaseModel):
        """
        Кандидат incident selection только из preview текущего Search artifact.

        `entity_id` — внутренний ID snapshot-а, нужен для stable option value.
        `number` — номер, который будет передан в IncidentRef после выбора.
        `label` — готовая компактная подпись для LLM и текстового question.
        """

        model_config = ConfigDict(extra="forbid")

        entity_id: str = Field(min_length=1)
        number: str = Field(min_length=1)
        label: str = Field(min_length=1)


    class IncidentSelectionDecision(BaseModel):
        """
        LLM-решение после выдачи Search, когда родительскому workflow нужен
        конкретный инцидент.

        select_incident:
        - question + минимум два preview entity_id;
        - runtime показывает реальные preview-кандидаты пользователю.

        refine_search:
        - Search выдача не позволяет безопасно выбрать preview candidate;
        - question просит сузить/уточнить запрос;
        - после ответа возвращаемся в Search Normalizer, не RCA.
        """

        model_config = ConfigDict(extra="forbid")

        action: IncidentSelectionAction
        question: str = Field(min_length=1)

        option_entity_ids: list[str] = Field(
            default_factory=list,
        )

        @model_validator(mode="after")
        def validate_shape(self) -> "IncidentSelectionDecision":
            if self.action == "select_incident":
                if not self.option_entity_ids:
                    raise ValueError(
                        "select_incident requires at least one option"
                    )

                if (
                    len(self.option_entity_ids)
                    != len(set(self.option_entity_ids))
                ):
                    raise ValueError(
                        "option_entity_ids must be unique"
                    )

            if self.action == "refine_search":
                if self.option_entity_ids:
                    raise ValueError(
                        "refine_search must not have options"
                    )

            return self


    class IncidentSelectionAnswer(BaseModel):
        """
        LLM interpretation обычного текстового ответа пользователя.

        selected:
        - entity_id обязан принадлежать опубликованным options.

        unclear:
        - entity_id отсутствует;
        - question содержит короткий повторный вопрос пользователю.
        """

        model_config = ConfigDict(extra="forbid")

        action: Literal["selected", "unclear"]
        entity_id: str | None = None
        question: str | None = None

        @model_validator(mode="after")
        def validate_shape(self) -> "IncidentSelectionAnswer":
            if self.action == "selected":
                if not self.entity_id:
                    raise ValueError(
                        "selected answer requires entity_id"
                    )

                if self.question is not None:
                    raise ValueError(
                        "selected answer must not contain question"
                    )

            if self.action == "unclear":
                if not self.question:
                    raise ValueError(
                        "unclear answer requires question"
                    )

                if self.entity_id is not None:
                    raise ValueError(
                        "unclear answer must not contain entity_id"
                    )

            return self