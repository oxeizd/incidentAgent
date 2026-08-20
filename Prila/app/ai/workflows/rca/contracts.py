from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.conversation import IncidentRef, IncidentReportRef


RCASourceKind = Literal[
    "incident",
    "report",
    "description",
    "search_result",
]

GateAction = Literal[
    "analyze",
    "clarify",
    "stop",
]

FactKind = Literal[
    "fact",
    "hypothesis",
    "unknown",
]

ConfidenceLevel = Literal[
    "low",
    "medium",
    "high",
]


class EvidenceItem(BaseModel):
    """
    Один проверяемый элемент RCA контекста.

    source_ref — optional: для свободного описания источником может быть
    пользовательское сообщение, а не доменный object. `source_label`
    позволяет показать происхождение факта в справке без передачи полного
    payload между workflow.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4_000)
    kind: FactKind
    source_ref: str | None = None
    source_label: str | None = None


class RCAInput(BaseModel):
    """
    Компактный сохранённый вход RCA.

    source_data — ограниченный snapshot загруженного incident/report context,
    который нужен gate/analyzer в рамках active task. Большие документы не
    должны попадать сюда бесконтрольно; memory adapter передаёт только
    согласованный доменный context.
    """

    model_config = ConfigDict(extra="forbid")

    source_kind: RCASourceKind

    incident_ref: IncidentRef | None = None
    report_ref: IncidentReportRef | None = None
    search_result_id: str | None = None
    raw_description: str | None = None

    source_data: dict[str, Any] = Field(default_factory=dict)
    user_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "RCAInput":
        if self.source_kind == "incident" and not self.incident_ref:
            raise ValueError(
                "incident source requires incident_ref"
            )

        if self.source_kind == "report" and not self.report_ref:
            raise ValueError(
                "report source requires report_ref"
            )

        if (
            self.source_kind == "description"
            and not self.raw_description
        ):
            raise ValueError(
                "description source requires raw_description"
            )

        if (
            self.source_kind == "search_result"
            and not self.search_result_id
        ):
            raise ValueError(
                "search_result source requires search_result_id"
            )

        return self


class RCAGateDecision(BaseModel):
    """
    Решение RCA gate после анализа текущего контекста.

    analyze:
    - фактов достаточно для анализа;
    - root cause может оставаться hypothesis, но это должно быть явно
      отражено в evidence/root_cause_kind и confidence.

    clarify:
    - нужны конкретные ответы пользователя;
    - questions содержит 1..3 коротких технических вопроса.

    stop:
    - RCA нельзя корректно продолжить: источник не найден, не является
      инцидентом либо пользовательский запрос не содержит incident context.
    """

    model_config = ConfigDict(extra="forbid")

    action: GateAction
    reason: str = Field(min_length=1, max_length=2_000)

    incident_summary: str = ""
    symptoms: list[str] = Field(default_factory=list)
    impact: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    applied_measures: list[str] = Field(default_factory=list)

    evidence: list[EvidenceItem] = Field(default_factory=list)

    root_cause: str | None = None
    root_cause_kind: FactKind = "unknown"
    causal_chain: list[str] = Field(default_factory=list)
    contributing_factors: list[str] = Field(default_factory=list)

    open_questions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    confidence: ConfidenceLevel
    confidence_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_gate_action(self) -> "RCAGateDecision":
        if self.action == "clarify":
            if not self.questions:
                raise ValueError(
                    "clarify action requires questions"
                )

            if len(self.questions) > 3:
                raise ValueError(
                    "clarify supports at most 3 questions"
                )

        if self.action == "analyze":
            if self.questions:
                raise ValueError(
                    "analyze action must not contain questions"
                )

        if self.action == "stop":
            if self.questions:
                raise ValueError(
                    "stop action must not contain questions"
                )

        if (
            self.root_cause_kind == "fact"
            and not self.root_cause
        ):
            raise ValueError(
                "fact root_cause_kind requires root_cause"
            )

        if (
            self.root_cause_kind == "hypothesis"
            and not self.root_cause
        ):
            raise ValueError(
                "hypothesis root_cause_kind requires root_cause"
            )

        return self


TaskType = Literal[
    "architecture",
    "config",
    "process",
    "monitoring",
    "automation",
]

TaskPriority = Literal[
    "high",
    "medium",
    "low",
]


class ProposedTask(BaseModel):
    """
    Системная corrective/preventive мера до validation against existing tasks.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=500)
    description: str = Field(min_length=3, max_length=5_000)
    addresses: str = Field(min_length=3, max_length=1_000)
    type: TaskType
    priority: TaskPriority
    expected_result: str = Field(
        min_length=3,
        max_length=2_000,
    )


class RCAReportDraft(BaseModel):
    """
    Полный draft будущей RCA-справки.

    `analysis` — готовый markdown для отчёта.
    Структурированные поля отдельно остаются source of truth для
    presentation/editor и для machine-readable persistence.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    affected_systems: list[str] = Field(default_factory=list)

    symptoms: list[str] = Field(default_factory=list)
    impact: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)

    facts: list[EvidenceItem] = Field(default_factory=list)
    root_cause: str | None = None
    root_cause_kind: FactKind = "unknown"

    causal_chain: list[str] = Field(default_factory=list)
    contributing_factors: list[str] = Field(default_factory=list)
    applied_measures: list[str] = Field(default_factory=list)

    corrective_actions: list[ProposedTask] = Field(
        default_factory=list
    )
    preventive_actions: list[ProposedTask] = Field(
        default_factory=list
    )

    open_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    confidence: ConfidenceLevel
    confidence_reason: str = Field(min_length=1)

    analysis: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_root_cause(self) -> "RCAReportDraft":
        if (
            self.root_cause_kind in {"fact", "hypothesis"}
            and not self.root_cause
        ):
            raise ValueError(
                "root_cause must be set when root_cause_kind is "
                "fact or hypothesis"
            )

        return self


ValidationStatus = Literal[
    "NEW",
    "PARTIAL",
    "DUPLICATE",
    "INVALID",
]


class ValidatedTask(ProposedTask):
    """
    Мера после task validator.

    Только tasks без INVALID должны попадать в финальный report.
    """

    validation_status: ValidationStatus
    validation_reason: str = Field(min_length=1)

    most_similar_assignment: dict[str, Any] | None = None


class RCAValidationResult(BaseModel):
    """
    Разделённый результат валидации actions.

    `accepted_tasks` сохраняются в report.
    `rejected_tasks` не исчезают бесследно: их можно отобразить пользователю
    либо записать в audit/history, но не включать как меры справки.
    """

    model_config = ConfigDict(extra="forbid")

    accepted_tasks: list[ValidatedTask] = Field(
        default_factory=list
    )
    rejected_tasks: list[ValidatedTask] = Field(
        default_factory=list
    )