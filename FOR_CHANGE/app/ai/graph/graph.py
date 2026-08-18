"""
Orchestrator graph construction and application dependency composition.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.ai.graph.build import build_subgraphs
from app.ai.graph.orchestrator.artifacts.registry import (
    build_default_artifact_handlers,
)
from app.ai.graph.orchestrator.classifier import IntentClassifier
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.dispatcher import IntentDispatcher
from app.ai.graph.orchestrator.handlers import BUILTIN_INTENT_HANDLERS
from app.ai.runtime.worker_runner import WorkerRunner
from app.ai.schemas.orchestrator import OrchestratorState
from memory.facade import MemoryFacade


def build_orchestrator_context(
    *,
    llm: Any,
    memory: MemoryFacade,
) -> OrchestratorContext:
    """
    Build application-scoped dependencies once during application startup.
    """
    subgraphs = build_subgraphs()

    context = OrchestratorContext(
        runner=WorkerRunner(subgraphs),
        classifier=IntentClassifier(llm),
        artifact_handlers=build_default_artifact_handlers(memory=memory),
        llm=llm,
    )
    context.dispatcher = IntentDispatcher(BUILTIN_INTENT_HANDLERS)

    return context


def build_orchestrator_graph(
    *,
    context: OrchestratorContext,
    checkpointer: Any,
) -> Any:
    """
    Build the top-level graph.

    The application lifecycle owns the context and checkpointer. This function
    has no process-global mutable dependencies or fallback checkpointers.
    """
    dispatcher = context.require_dispatcher()

    async def classify_intent(
        state: OrchestratorState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        decision = await context.classifier.classify(state)
        return decision.to_state_update()

    async def dispatch_intent(
        state: OrchestratorState,
        config: RunnableConfig,
    ) -> Command:
        return await dispatcher.dispatch(
            state,
            config,
            context,
        )

    graph = StateGraph(OrchestratorState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("dispatch_intent", dispatch_intent)

    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "dispatch_intent")
    graph.add_edge("dispatch_intent", END)

    return graph.compile(checkpointer=checkpointer)