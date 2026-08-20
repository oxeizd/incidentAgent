from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from app.ai.schemas.artifact import Artifact
from app.ai.schemas.conversation import ConversationTask

from app.ai.schemas.conversation import (
    ConversationTask,
    LastSearchContext,
)


class ConversationState(TypedDict):
    """
    Новый root state единого ассистента.

    Содержит ровно одну активную и одну отложенную задачу. Полные документы,
    incident payloads и search rows не должны попадать сюда: хранится только
    минимальное состояние task + domain references.
    """

    messages: Annotated[list[AnyMessage], add_messages]

    user_id: str
    thread_id: str

    active_task: ConversationTask | None
    suspended_task: ConversationTask | None

    artifacts: dict[str, Artifact]
    current_artifact_id: str | None
    last_search: LastSearchContext | None

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
    """
    Factory нового root state.

    Время передаётся снаружи: это упрощает тестирование и позволяет
    transport слою оставаться единственным владельцем time policy.
    """
    return {
        "messages": [first_message],
        "user_id": user_id,
        "thread_id": thread_id,
        "active_task": None,
        "suspended_task": None,
        "artifacts": {},
        "current_artifact_id": None,
        "last_search": None,
        "turn_count": 0,
        "session_start_at": session_start_at,
    }