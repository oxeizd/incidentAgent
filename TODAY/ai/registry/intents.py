from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.types import Command

    from app.ai.schemas.orchestrator import OrchestratorState


IntentHandler = Callable[
    ["OrchestratorState", dict[str, Any], dict[str, Any]],
    Awaitable["Command"],
]


INTENT_REGISTRY: dict[str, IntentHandler] = {}


def register_intent(
    name: str,
) -> Callable[[IntentHandler], IntentHandler]:
    """
    Декоратор регистрации orchestrator intent handler-а.
    """

    def decorator(handler: IntentHandler) -> IntentHandler:
        if name in INTENT_REGISTRY:
            raise ValueError(
                f"Intent handler {name!r} is already registered"
            )

        INTENT_REGISTRY[name] = handler
        return handler

    return decorator