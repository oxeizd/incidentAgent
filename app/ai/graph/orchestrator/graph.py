"""
app/ai/graph/orchestrator/graph.py

Тонкая wiring-точка orchestration graph. Здесь только:
  1. кэширование compiled worker subgraphs через WorkerRunner;
  2. composition root (classifier, artifact registry, handlers/dispatcher);
  3. явный LangGraph control flow START -> classify_intent -> run_worker -> END.

Ни RCA/report artifact lifecycle, ни presentation persistence, ни edit parsing,
ни routing domain workflows здесь не живут — это isolated handlers и result
handlers. Такой же explicit graph style сохраняет читаемость, как
app/ai/graph/build.py.
"""
from __future__ import annotations
from typing import Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.ai.graph.build import build_subgraphs
from app.ai.graph.orchestrator.artifacts.registry import build_default_artifact_handlers
from app.ai.graph.orchestrator.classifier import IntentClassifier, classify_intent
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.dispatcher import IntentDispatcher
from app.ai.graph.orchestrator.handlers import BUILTIN_INTENT_HANDLERS
from app.ai.runtime.worker_runner import WorkerRunner
from app.ai.schemas.orchestrator import OrchestratorState
from app.services.llm import llm_client

_context: Optional[OrchestratorContext] = None


def get_orchestrator_context() -> OrchestratorContext:
    """
    Process-local composition root. Worker subgraphs и handler mapping не
    имеют request-specific состояния, поэтому создаются один раз. В тестах
    можно не использовать этот singleton: создать OrchestratorContext с fake
    WorkerRunner/LLM и передать его в dispatch_intent().
    """
    global _context
    if _context is None:
        subgraphs = build_subgraphs()
        context = OrchestratorContext(
            runner=WorkerRunner(subgraphs),
            subgraphs=subgraphs,
            classifier=IntentClassifier(llm_client),
            artifact_handlers=build_default_artifact_handlers(),
            llm=llm_client,
        )
        context.dispatcher = IntentDispatcher(BUILTIN_INTENT_HANDLERS)
        _context = context
    return _context


async def dispatch_intent(
    state: OrchestratorState,
    config,
    *,
    context: Optional[OrchestratorContext] = None,
) -> Command:
    """Testable graph-node implementation for the post-classification step."""
    ctx = context or get_orchestrator_context()
    assert ctx.dispatcher is not None
    return await ctx.dispatcher.dispatch(state, config, ctx)


async def run_worker(state: OrchestratorState, config) -> Command:
    """Backward-compatible graph node name retained for compiled checkpoints."""
    return await dispatch_intent(state, config)


def build_orchestrator_graph(checkpointer=None):
    graph = StateGraph(OrchestratorState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("run_worker", run_worker)
    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "run_worker")
    graph.add_edge("run_worker", END)

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)
