from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskKind = Literal[
    "search",
    "rca",
    "edit",
    "presentation",
]

TaskStatus = Literal[
    "active",
    "awaiting_input",
    "suspended",
    "completed",
    "cancelled",
    "failed",
]

InteractionKind = Literal[
    "free_text",
    "confirm",
    "single_select",
    "multi_select",
    "form",
]

PlanIntent = Literal[
    "search",
    "rca",
    "presentation",
    "edit",
    "chat",
]

RefKind = Literal[
    "incident",
    "search_result",
    "incident_report",
    "presentation",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DomainRef(BaseModel):
    """
    Компактная стабильная ссылка на объект memory/domain layer.

    В state и между workflow передаём только ref, а полные данные загружаем
    через MemoryFacade именно там, где они нужны.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RefKind
    id: str = Field(min_length=1)
    label: str | None = None


class IncidentRef(DomainRef):
    kind: Literal["incident"] = "incident"
    number: str | None = None


class SearchResultRef(DomainRef):
    kind: Literal["search_result"] = "search_result"


class LastSearchContext(BaseModel):
    """
    Компактный итог последнего завершённого самостоятельного поиска.

    Не хранит tool calls, history, preview rows или полный search artifact.
    Достаточен, чтобы Planner понимал короткие продолжения:
    «только за май», «по другой системе», «сделай RCA по первому».
    """

    model_config = ConfigDict(extra="forbid")

    result_ref: SearchResultRef
    plan: dict[str, Any]
    goal: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)


class IncidentReportRef(DomainRef):
    kind: Literal["incident_report"] = "incident_report"


class PresentationRef(DomainRef):
    kind: Literal["presentation"] = "presentation"


class SelectOption(BaseModel):
    """
    Вариант для single_select/multi_select.

    `value` — стабильное машинное значение, которое UI возвращает graph.
    `label` — человекочитаемый текст для пользователя.
    """

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str | None = None
    ref: DomainRef | None = None


class FormField(BaseModel):
    """
    Описание поля формы без transport-specific деталей UI.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    required: bool = False
    placeholder: str | None = None
    help_text: str | None = None


class Interaction(BaseModel):
    """
    Единственный публичный контракт вопроса от workflow к UI/API.

    continuation_stage — точка возврата внутри workflow. Runtime не должен
    угадывать, куда возобновить выполнение после ответа пользователя.
    """

    model_config = ConfigDict(extra="forbid")

    interaction_id: str = Field(min_length=1)
    owner: TaskKind
    continuation_stage: str = Field(min_length=1)

    kind: InteractionKind
    question: str = Field(min_length=1)

    options: list[SelectOption] = Field(default_factory=list)
    fields: list[FormField] = Field(default_factory=list)

    preview: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> "Interaction":
        if self.kind in {"single_select", "multi_select"}:
            if not self.options:
                raise ValueError(
                    "select interaction requires at least one option"
                )

            if self.fields:
                raise ValueError(
                    "select interaction cannot contain form fields"
                )

        if self.kind == "form" and not self.fields:
            raise ValueError(
                "form interaction requires at least one field"
            )

        if self.kind != "form" and self.fields:
            raise ValueError(
                "only form interaction may contain fields"
            )

        if self.kind not in {"single_select", "multi_select"}:
            if self.options:
                raise ValueError(
                    "only select interaction may contain options"
                )

        return self


class TaskSnapshot(BaseModel):
    """
    Сериализуемая точка продолжения конкретного workflow.

    `stage` — текущая стадия workflow.
    `data` — минимальное workflow-specific состояние. Здесь допустимы IDs,
    нормализованные планы, ответы пользователя и промежуточные выводы LLM,
    но не runtime сервисы, LangChain objects, tools или большие документы.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


class AgentHistoryEvent(BaseModel):
    """
    Короткое сериализуемое событие локальной истории агента.

    Не хранит chain-of-thought, LangChain messages или runtime объекты.
    payload содержит только полезный воспроизводимый контекст: текст
    пользователя, публичный вопрос, typed decision, tool args/result,
    ref или план.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal[
        "user",
        "assistant",
        "tool",
        "system",
    ]

    kind: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)


class AgentHistory(BaseModel):
    """
    Bounded локальная история одного агента/workflow.

    history хранится внутри task snapshot, следовательно автоматически
    исчезает из active/suspended runtime state при terminal завершении task.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)
    max_events: int = Field(default=24, ge=4, le=100)
    events: list[AgentHistoryEvent] = Field(default_factory=list)

    def append(
        self,
        event: AgentHistoryEvent,
    ) -> "AgentHistory":
        bounded_events = [
            *self.events,
            event,
        ][-self.max_events :]

        return self.model_copy(
            update={
                "events": bounded_events,
            }
        )


class ConversationTask(BaseModel):
    """
    Пользовательская единица незавершённой работы.

    В root state допускаются только:
    - одна active_task;
    - одна suspended_task.

    Worker graph, LangGraph checkpoint и технические детали могут жить
    отдельно, но lifecycle пользователя описывается только этой моделью.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    kind: TaskKind

    goal: str = Field(min_length=1)
    status: TaskStatus

    snapshot: TaskSnapshot
    refs: list[DomainRef] = Field(default_factory=list)

    pending_interaction: Interaction | None = None

    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    suspended_at: str | None = None
    suspension_reason: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ConversationTask":
        if (
            self.status == "awaiting_input"
            and self.pending_interaction is None
        ):
            raise ValueError(
                "awaiting_input task requires pending_interaction"
            )

        if (
            self.status != "awaiting_input"
            and self.pending_interaction is not None
        ):
            raise ValueError(
                "only awaiting_input task may have pending_interaction"
            )

        if self.status == "suspended":
            if not self.suspended_at:
                raise ValueError(
                    "suspended task requires suspended_at"
                )

            if not self.suspension_reason:
                raise ValueError(
                    "suspended task requires suspension_reason"
                )

        return self


class ConversationPlan(BaseModel):
    """
    Структурированный план LLM planner-а для нового пользовательского запроса.

    Planner не обращается к memory, не сохраняет артефакты и не запускает
    workflow. Он только фиксирует пользовательскую цель и доступные входы.
    """

    model_config = ConfigDict(extra="forbid")

    intent: PlanIntent
    goal: str = Field(min_length=1)

    requires_search: bool = False

    incident_number: str | None = None
    raw_description: str | None = None

    use_current_report: bool = False
    target_ref: DomainRef | None = None

    search_query: str | None = None
    edit_instruction: str | None = None

    chat_response: str | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> "ConversationPlan":
        if self.intent == "chat":
            if not self.chat_response:
                raise ValueError(
                    "chat plan requires chat_response"
                )

            return self

        if self.intent == "search" and not self.search_query:
            raise ValueError(
                "search plan requires search_query"
            )

        if self.intent == "rca":
            has_source = any(
                (
                    self.incident_number,
                    self.raw_description,
                    self.target_ref
                    and self.target_ref.kind
                    in {"incident", "incident_report"},
                )
            )

            if not has_source and not self.requires_search:
                raise ValueError(
                    "rca plan requires source or requires_search=true"
                )

        if self.intent == "presentation":
            has_source = any(
                (
                    self.incident_number,
                    self.raw_description,
                    self.use_current_report,
                    self.target_ref,
                )
            )

            if not has_source and not self.requires_search:
                raise ValueError(
                    "presentation plan requires source or search"
                )

        if self.intent == "edit":
            if not self.edit_instruction:
                raise ValueError(
                    "edit plan requires edit_instruction"
                )

            if not (
                self.use_current_report
                or (
                    self.target_ref
                    and self.target_ref.kind == "incident_report"
                )
            ):
                raise ValueError(
                    "edit plan requires incident report target"
                )

        return self