from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RCAInputKind = Literal[
    "incident",
    "report",
    "description",
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

GateMode = Literal[
    "readiness",
    "quality",
]

ReadinessAction = Literal[
    "proceed",
    "ask_user",
    "stop",
]

QualityAction = Literal[
    "approve",
    "revise",
    "ask_user",
]

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

ValidationStatus = Literal[
    "NEW",
    "PARTIAL",
    "DUPLICATE",
    "INVALID",
]


class EvidenceItem(BaseModel):
    """Одно утверждение RCA с явно указанным уровнем достоверности."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4_000)
    kind: FactKind

    source_ref: str | None = None
    source_label: str | None = None


class RCAInput(BaseModel):
    """
    Текущий источник RCA-анализа.

    `source_payload` передаётся только между функциями внутри одного запуска
    worker-а. Его нельзя класть в TaskSnapshot, StepRun history или refs:
    полный incident/report всегда повторно загружается по ссылке на resume.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RCAInputKind

    incident_number: str | None = None
    report_id: str | None = None
    raw_description: str | None = None

    source_payload: dict[str, Any] = Field(default_factory=dict)


class RCAInvestigation(BaseModel):
    """
    Структурированный результат исследовательской фазы RCA Agent.

    Агент использует доступные tools и возвращает только evidence/hypotheses,
    вопросы и компактный контекст для gate/analyzer. Он не сохраняет report.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=5_000)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[EvidenceItem] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list, max_length=5)


class RCAReadinessDecision(BaseModel):
    """Решение обязательного RCA Gate до генерации или редактирования draft."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["readiness"] = "readiness"
    action: ReadinessAction
    reason: str = Field(min_length=1, max_length=2_000)

    questions: list[str] = Field(default_factory=list, max_length=3)


class ProposedTask(BaseModel):
    """Предложенное corrective/preventive действие до проверки дублей."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=500)
    description: str = Field(min_length=3, max_length=5_000)
    addresses: str = Field(min_length=3, max_length=1_000)
    type: TaskType
    priority: TaskPriority
    expected_result: str = Field(min_length=3, max_length=2_000)


class ValidatedTask(ProposedTask):
    """Поручение после проверки against existing assignments."""

    validation_status: ValidationStatus
    validation_reason: str = Field(min_length=1, max_length=2_000)
    most_similar_assignment: dict[str, Any] | None = None


class RCAReportDraft(BaseModel):
    """
    Полный machine-readable и user-facing draft RCA-справки.

    `analysis` — Markdown для пользователя. Остальные поля сохраняются как
    report sections, могут использоваться UI и presentation worker-ом.
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

    corrective_actions: list[ProposedTask] = Field(default_factory=list)
    preventive_actions: list[ProposedTask] = Field(default_factory=list)

    open_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    confidence: ConfidenceLevel
    confidence_reason: str = Field(min_length=1)

    analysis: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_root_cause(self) -> "RCAReportDraft":
        if self.root_cause_kind in {"fact", "hypothesis"}:
            if not self.root_cause or not self.root_cause.strip():
                raise ValueError(
                    "fact or hypothesis root cause requires text"
                )

        return self


class RCAValidationResult(BaseModel):
    """Итог проверки corrective/preventive actions."""

    model_config = ConfigDict(extra="forbid")

    accepted_tasks: list[ValidatedTask] = Field(default_factory=list)
    rejected_tasks: list[ValidatedTask] = Field(default_factory=list)


class RCAQualityDecision(BaseModel):
    """Решение обязательного RCA Gate по качеству готового draft."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["quality"] = "quality"
    action: QualityAction
    reason: str = Field(min_length=1, max_length=2_000)

    required_changes: list[str] = Field(default_factory=list, max_length=10)
    questions: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "RCAQualityDecision":
        if self.action == "revise" and not self.required_changes:
            raise ValueError("revise action requires required_changes")

        if self.action == "ask_user" and not self.questions:
            raise ValueError("ask_user action requires questions")

        return self