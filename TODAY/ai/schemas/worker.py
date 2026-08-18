from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


WorkerKind = Literal[
    "search",
    "rca",
    "editor",
    "creator",
]

WorkerStatus = Literal[
    "running",
    "awaiting_user_input",
    "done",
    "failed",
    "cancelled",
    "deviated",
]


class WorkerState(TypedDict):
    """
    Изолированное состояние одного workflow/subgraph.

    Worker state живёт в orchestrator workers map. Его payload валидируется
    схемой, зарегистрированной для конкретного WorkerKind.

    history — локальная история output-ов worker-а. Она не заменяет главную
    историю user/assistant сообщений в OrchestratorState.messages.
    """

    worker_id: str
    kind: WorkerKind
    parent_worker_id: str | None

    history: Annotated[list[AnyMessage], add_messages]

    input_context: dict
    payload: dict

    status: WorkerStatus
    rounds: int
    max_rounds: int

    summary_for_parent: dict | None
    produced_artifact_refs: list[str]

    error: NotRequired[dict]
    reasoning_events: NotRequired[list[dict]]