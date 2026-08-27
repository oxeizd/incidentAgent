from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TaskStatus = Literal[
    "running",
    "waiting_for_user",
    "suspended",
    "completed",
    "cancelled",
    "failed",
]


StepKind = Literal[
    "search",
    "rca",
    "presentation",
]


StepStatus = Literal[
    "pending",
    "running",
    "waiting_for_user",
    "completed",
    "cancelled",
    "failed",
    "skipped",
]


UserInputKind = Literal[
    "ask_user",
    "ask_confirmation",
]


PlannerDecisionKind = Literal[
    "execute",
    "chat",
]


ArtifactKind = Literal[
    "incident",
    "search_result",
    "analytics_result",
    "incident_report",
    "presentation",
]


AgentMessageRole = Literal[
    "user",
    "assistant",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactRef(BaseModel):
    """
    Стабильная ссылка на сохранённый доменный артефакт.

    Полные результаты поиска, RCA-справки и презентации не хранятся в
    conversation checkpoint. Workflow загружает их из memory layer только
    по этой ссылке, когда они нужны.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    id: str = Field(min_length=1)
    label: str | None = None


class IncidentRef(ArtifactRef):
    kind: Literal["incident"] = "incident"
    number: str | None = None


class SearchResultRef(ArtifactRef):
    kind: Literal["search_result"] = "search_result"


class AnalyticsResultRef(ArtifactRef):
    kind: Literal["analytics_result"] = "analytics_result"


class IncidentReportRef(ArtifactRef):
    kind: Literal["incident_report"] = "incident_report"


class PresentationRef(ArtifactRef):
    kind: Literal["presentation"] = "presentation"


class AgentMessage(BaseModel):
    """
    Обычное сообщение локального диалога одного шага.

    Оно сериализуется в checkpoint как Pydantic-модель, а перед вызовом LLM
    конвертируется в HumanMessage или AIMessage. System prompt и tool output
    сюда не записываются.
    """

    model_config = ConfigDict(extra="forbid")

    role: AgentMessageRole
    content: str = Field(min_length=1)
    created_at: str = Field(min_length=1)


class AgentConversation(BaseModel):
    """
    Ограниченная рабочая переписка конкретного шага ExecutionPlan.

    Search, RCA и presentation не делят историю друг с другом. После
    завершения задачи она исчезает вместе с TaskSnapshot; в memory layer
    остаются только итоговые артефакты.
    """

    model_config = ConfigDict(extra="forbid")

    max_messages: int = Field(default=24, ge=4, le=100)
    messages: list[AgentMessage] = Field(default_factory=list)

    def append(
        self,
        *,
        role: AgentMessageRole,
        content: str,
    ) -> "AgentConversation":
        return self.model_copy(
            update={
                "messages": [
                    *self.messages,
                    AgentMessage(
                        role=role,
                        content=content,
                        created_at=utc_now_iso(),
                    ),
                ][-self.max_messages:],
            }
        )


class UserInputRequest(BaseModel):
    """
    Запрос workflow к пользователю.

    `step_id` — шаг, который задал вопрос и который продолжит работу после
    ответа. Сейчас поддерживаются только текстовое уточнение и подтверждение.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)

    kind: UserInputKind
    question: str = Field(min_length=1)

    preview: dict[str, Any] | None = None


class PlanStep(BaseModel):
    """
    Один шаг исполнимого графа.

    `depends_on` — ID шагов, которые должны завершиться до его запуска.
    Независимые шаги executor запускает параллельно.
    `inputs` содержит только JSON-совместимые параметры и artifact refs.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    kind: StepKind
    goal: str = Field(min_length=1)

    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)

    user_visible_label: str = Field(min_length=1)


class ExecutionPlan(BaseModel):
    """
    Неизменяемый DAG одной пользовательской задачи.

    Planner создаёт план. Executor использует зависимости, запускает готовые
    шаги, сохраняет output refs и управляет отменой/возобновлением.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1)
    created_at: str = Field(min_length=1)


class StepRun(BaseModel):
    """
    Изменяемое состояние одного шага ExecutionPlan.

    История принадлежит именно этому запуску шага. Она позволяет агенту
    задавать уточняющие вопросы и продолжать работу после checkpoint-а.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    status: StepStatus = "pending"

    output_refs: list[ArtifactRef] = Field(default_factory=list)
    error: str | None = None

    conversation: AgentConversation = Field(
        default_factory=AgentConversation
    )

    started_at: str | None = None
    completed_at: str | None = None


class PlannerDecision(BaseModel):
    """
    Результат planner-а для нового пользовательского сообщения.

    `chat` возвращает готовый ответ без исполнения задачи.
    `execute` возвращает полный ExecutionPlan для executor-а.
    """

    model_config = ConfigDict(extra="forbid")

    kind: PlannerDecisionKind
    chat_response: str | None = None
    plan: ExecutionPlan | None = None


class TaskSnapshot(BaseModel):
    """
    Сериализуемое состояние текущего выполнения ExecutionPlan.

    План не изменяется. Меняются только StepRun: статусы, история, ссылки на
    результаты, ошибки и время исполнения.
    """

    model_config = ConfigDict(extra="forbid")

    plan: ExecutionPlan
    step_runs: dict[str, StepRun] = Field(default_factory=dict)


class ConversationTask(BaseModel):
    """
    Одна пользовательская задача с DAG внутри.

    В корневом state одновременно есть максимум одна текущая задача и одна
    отложенная. Параллельные ветки существуют внутри ExecutionPlan.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    status: TaskStatus

    snapshot: TaskSnapshot
    pending_user_input: UserInputRequest | None = None

    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    suspended_at: str | None = None
    suspension_reason: str | None = None