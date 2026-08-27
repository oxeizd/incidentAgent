from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from app.ai.schemas.conversation import (
    ConversationTask,
    IncidentReportRef,
    PresentationRef,
)


class ConversationState(TypedDict):
    """
    Корневое состояние одного пользовательского чата.

    В state хранятся сообщения чата, lifecycle текущей/отложенной задачи и
    ссылки на последние пользовательские результаты. Сам ExecutionPlan,
    StepRun и локальные диалоги агентов находятся внутри ConversationTask.

    Полные результаты поиска, RCA-справки, презентации, tool output и runtime
    services здесь не сохраняются.
    """

    messages: Annotated[list[AnyMessage], add_messages]

    user_id: str
    thread_id: str

    active_task: ConversationTask | None
    suspended_task: ConversationTask | None

    current_report_ref: IncidentReportRef | None
    current_presentation_ref: PresentationRef | None

    turn_count: int
    session_start_at: str

    last_error_message: NotRequired[str | None]
    last_status: NotRequired[str | None]


def build_initial_conversation_state(
    *,
    user_id: str,
    thread_id: str,
    first_message: AnyMessage,
    session_start_at: str,
) -> ConversationState:
    """Создаёт начальное состояние нового диалога."""
    return {
        "messages": [first_message],
        "user_id": user_id,
        "thread_id": thread_id,
        "active_task": None,
        "suspended_task": None,
        "current_report_ref": None,
        "current_presentation_ref": None,
        "turn_count": 0,
        "session_start_at": session_start_at,
    }


def coerce_conversation_state(
    state: ConversationState,
) -> ConversationState:
    """
    Восстанавливает Pydantic-модели после загрузки checkpoint-а.

    LangGraph сохраняет TypedDict как JSON-like данные. На следующем turn
    модели приходят обратно обычными dict, поэтому их нужно привести один раз
    на входе в conversation router.
    """
    coerced: dict[str, Any] = dict(state)

    active_task = coerced.get("active_task")
    if isinstance(active_task, dict):
        coerced["active_task"] = ConversationTask.model_validate(
            active_task
        )

    suspended_task = coerced.get("suspended_task")
    if isinstance(suspended_task, dict):
        coerced["suspended_task"] = ConversationTask.model_validate(
            suspended_task
        )

    current_report_ref = coerced.get("current_report_ref")
    if isinstance(current_report_ref, dict):
        coerced["current_report_ref"] = IncidentReportRef.model_validate(
            current_report_ref
        )

    current_presentation_ref = coerced.get("current_presentation_ref")
    if isinstance(current_presentation_ref, dict):
        coerced["current_presentation_ref"] = (
            PresentationRef.model_validate(current_presentation_ref)
        )

    return coerced