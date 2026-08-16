from __future__ import annotations
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.types import Command
    from app.ai.schemas.orchestrator import OrchestratorState

IntentHandler = Callable[["OrchestratorState", Any, dict], Awaitable["Command"]]
INTENT_REGISTRY: dict[str, IntentHandler] = {}


def register_intent(name: str):
    def deco(fn: IntentHandler) -> IntentHandler:
        if name in INTENT_REGISTRY:
            raise ValueError(f"intent '{name}' already registered")
        INTENT_REGISTRY[name] = fn
        return fn
    return deco