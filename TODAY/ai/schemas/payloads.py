from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PayloadSchema(BaseModel):
    """
    Базовый payload worker-а.

    Payload хранит изменяемые сериализуемые данные конкретного workflow.
    Долгоживущий контекст передаётся через worker.input_context.
    """

    model_config = ConfigDict(extra="forbid")


class SearchPayload(PayloadSchema):
    """
    Данные search worker-а.

    resolver_* поля нужны для interrupt-safe entity resolution:
    LangGraph повторно запускает node с начала при Command(resume=...),
    поэтому решение, по которому был задан вопрос, должно быть сохранено
    в payload до interrupt.
    """

    raw_query: str = Field(min_length=1)
    resolved_query: str | None = None

    resolver_stage: Literal[
        "new",
        "awaiting_selection",
        "resolved",
    ] = "new"

    resolver_base_query: str | None = None
    resolver_question: str | None = None
    resolver_options: list[str] = Field(default_factory=list)

    search_mode: Literal[
        "structured",
        "semantic_similarity",
        "mixed",
    ] | None = None

    result_entity: Literal[
        "incidents",
        "assignments",
        "mixed",
    ] | None = None

    result_count: int = Field(default=0, ge=0)
    results: list[dict[str, Any]] = Field(default_factory=list)


class RCAPayload(PayloadSchema):
    """
    Изменяемые результаты RCA workflow.

    Вход RCA лежит в input_context как один из:
      - incident_number;
      - raw_description;
      - search_summary.
    """

    gate_status: Literal[
        "root_cause_present",
        "insufficient_info",
        "contradictory_or_unclear",
        "no_incident_data",
    ] | None = None

    incident_summary: str = ""
    impact: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    mitigation: list[str] = Field(default_factory=list)

    suspected_root_cause: str | None = None
    root_cause_present: bool = False
    root_cause_statement: str | None = None
    causal_chain: list[str] = Field(default_factory=list)
    evidence_found: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reason: str = ""

    user_answers: dict[str, str] = Field(default_factory=dict)

    analysis: str = ""
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    validated_tasks: list[dict[str, Any]] = Field(default_factory=list)


class EditorPayload(PayloadSchema):
    """
    Payload правки versioned incident_report artifact.
    """

    target_artifact_id: str = Field(min_length=1)
    target_section: Literal["analysis", "tasks"]
    instruction: str = Field(min_length=1)

    task_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "0-based индекс меры в tasks. Если None, редактируется "
            "целиком указанная текстовая секция."
        ),
    )

    proposed_diff: dict[str, Any] | None = None
    applied_patches: list[dict[str, Any]] = Field(default_factory=list)


class CreatorPayload(PayloadSchema):
    """
    Структурированные поля будущего PresentationDocument.

    HTML не хранится в payload и не создаётся AI-слоем.
    """

    source_artifact_id: str | None = None
    collected: dict[str, Any] = Field(default_factory=dict)


WorkerPayload = (
    SearchPayload
    | RCAPayload
    | EditorPayload
    | CreatorPayload
)