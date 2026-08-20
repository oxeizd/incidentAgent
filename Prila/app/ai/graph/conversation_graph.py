from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.ai.graph.conversation_router import handle_user_turn
from app.ai.schemas.conversation_state import ConversationState


def build_conversation_graph(
    *,
    checkpointer: Any | None = None,
):
    """
    Единый graph одного хода пользователя.

    Transport добавляет HumanMessage в ConversationState перед запуском.
    Router выполняет Guard → Planner/Dispatcher → workflow.
    """
    graph = StateGraph(ConversationState)

    graph.add_node(
        "handle_user_turn",
        handle_user_turn,
    )

    graph.add_edge(
        START,
        "handle_user_turn",
    )
    graph.add_edge(
        "handle_user_turn",
        END,
    )

    return graph.compile(checkpointer=checkpointer)


conversation_graph = build_conversation_graph()