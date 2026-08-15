from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated, Optional
from typing_extensions import TypedDict, NotRequired
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage

from app.ai.schemas.worker import WorkerState
from app.ai.schemas.artifact import Artifact


class PlanFrame(TypedDict):
    worker_id: str
    reason_suspended: str
    created_at: str


class OrchestratorState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: str
    intent_confidence: NotRequired[float]
    _incident_number: NotRequired[Optional[str]]
    _raw_description: NotRequired[Optional[str]]
    _resolved_query: NotRequired[Optional[str]]
    _evidence: NotRequired[Optional[str]]
    focus_worker_id: Optional[str]
    plan_stack: list[PlanFrame]
    workers: dict[str, WorkerState]
    artifacts: dict[str, Artifact]
    current_artifact_id: Optional[str]
    pending_interrupt: Optional[dict]
    completed_workers: NotRequired[list[str]]
    thread_id: str
    turn_count: int
    session_start_at: str


def build_initial_state(thread_id: str, first_message: AnyMessage) -> OrchestratorState:
    """
    Единственный источник правды о "пустом" состоянии нового треда.

    ИСПРАВЛЕНО: раньше этот же набор полей был продублирован ЛИТЕРАЛОМ dict
    прямо в app/api/app.py:_build_graph_input (единственном месте, где решается
    "это новый тред -> нужен полный OrchestratorState"). Схема и её начальное значение
    задавались в двух разных файлах — при добавлении/переименовании
    NotRequired-поля здесь легко забыть обновить тот литерал в app.py, и узел
    classify_intent упадёт на KeyError на первом шаге нового треда (см.
    app/ai/graph/orchestrator.py:classify_intent — читает state["messages"]
    и т.п. без .get()). теперь app.py вызывает эту функцию, а не строит
    dict вручную — расхождение схемы и начального состояния физически
    невозможно, они меняются одним диффом в одном файле.

    NotRequired-поля (intent_confidence, _incident_number, _raw_description,
    _resolved_query, _evidence, completed_workers) сознательно не включены —
    это транзитные поля одного шага графа (заполняются classify_intent на
    каждом вызове), а не часть "пустого" состояния нового треда.
    """
    return {
        "messages": [first_message],
        "intent": "",
        "focus_worker_id": None,
        "plan_stack": [],
        "workers": {},
        "artifacts": {},
        "current_artifact_id": None,
        "pending_interrupt": None,
        "thread_id": thread_id,
        "turn_count": 0,
        "session_start_at": datetime.now(timezone.utc).isoformat(),
    }
