from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from app.ai.schemas.artifact import Artifact
from app.ai.schemas.worker import WorkerState


IntentName = Literal[
    "new_search",
    "search_then_analyze",
    "analyze",
    "resume_previous",
    "edit_report",
    "reanalyze_report",
    "create_presentation",
    "cancel_current",
    "chitchat_or_other",
]


class PlanFrame(TypedDict):
    """
    Отложенный worker.

    Используется, когда пользователь временно ушёл в другой intent, а затем
    возвращается к исходной незавершённой задаче.
    """

    worker_id: str
    reason_suspended: str
    created_at: str


class PendingInterrupt(TypedDict):
    """
    Минимальная runtime-информация об ожидающем user input.

    Полный интерактивный payload живёт в checkpoint interrupts LangGraph.
    Здесь храним только удобные orchestrator-метаданные для intent routing.
    """

    worker_id: str
    question: str
    interaction_type: Literal[
        "question",
        "confirmation",
        "form",
    ]


class OrchestratorState(TypedDict):
    """
    Корневой serializable state LangGraph conversation.

    `user_id` устанавливается transport/API при первом graph invocation,
    никогда не извлекается из текста пользователя и не доверяется LLM.
    """

    messages: Annotated[list[AnyMessage], add_messages]

    user_id: str
    thread_id: str

    intent: IntentName | str
    intent_confidence: NotRequired[float]

    # One-turn planner output. Нужен только текущему запуску orchestrator.
    _incident_number: NotRequired[str | None]
    _raw_description: NotRequired[str | None]
    _resolved_query: NotRequired[str | None]
    _evidence: NotRequired[str | None]

    focus_worker_id: str | None
    plan_stack: list[PlanFrame]

    workers: dict[str, WorkerState]

    artifacts: dict[str, Artifact]
    current_artifact_id: str | None

    pending_interrupt: PendingInterrupt | None

    turn_count: int
    session_start_at: str

    completed_workers: NotRequired[list[str]]


def build_initial_state(
    *,
    user_id: str,
    thread_id: str,
    first_message: AnyMessage,
) -> OrchestratorState:
    """
    Единственный factory для нового conversation graph state.

    API adapter должен вызывать эту функцию при отсутствии checkpoint,
    а не собирать initial state dict вручную.
    """
    now = datetime.now(timezone.utc).isoformat()

    return {
        "messages": [first_message],
        "user_id": user_id,
        "thread_id": thread_id,
        "intent": "",
        "focus_worker_id": None,
        "plan_stack": [],
        "workers": {},
        "artifacts": {},
        "current_artifact_id": None,
        "pending_interrupt": None,
        "turn_count": 0,
        "session_start_at": now,
    }