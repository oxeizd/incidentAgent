from __future__ import annotations
from typing import TYPE_CHECKING
from langchain_core.messages import AIMessage
from langgraph.types import Command
from app.ai.runtime.factory import spawn_worker, SpawnError

if TYPE_CHECKING:
    from app.ai.schemas.orchestrator import OrchestratorState


def spawn_or_respond(kind: str, input_context: dict, state: "OrchestratorState"):
    result = spawn_worker(kind, input_context, state)
    if isinstance(result, SpawnError):
        return Command(update={"messages": [AIMessage(content=result.to_user_message())]})
    return result