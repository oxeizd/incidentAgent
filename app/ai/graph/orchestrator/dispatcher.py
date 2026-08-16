"""
app/ai/graph/orchestrator/dispatcher.py

Wave 5: заменяет плоский декоратор-based INTENT_REGISTRY, который раньше
жил прямо в app/ai/graph/orchestrator.py вперемешку с бизнес-логикой каждого
интента. IntentDispatcher не знает НИЧЕГО про RCA/presentation/edit — каждый
handler — самостоятельный модуль в app/ai/graph/orchestrator/handlers/.

app/ai/registry/intents.py (старый декоратор-based реестр) оставлен как есть
для обратной совместимости, если где-то есть внешний код, который на него
завязан — новый dispatcher его не использует и не требует.
"""
from __future__ import annotations
from typing import Mapping, Protocol

from langgraph.types import Command
from langchain_core.messages import AIMessage

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.graph.orchestrator.context import OrchestratorContext


class IntentHandler(Protocol):
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command: ...


class _UnknownIntentHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        return Command(update={"messages": [AIMessage(content="Не понял, что нужно сделать.")]})


class IntentDispatcher:
    def __init__(self, handlers: Mapping[str, IntentHandler]) -> None:
        self._handlers = dict(handlers)
        self._fallback: IntentHandler = _UnknownIntentHandler()

    async def dispatch(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        handler = self._handlers.get(state["intent"], self._fallback)
        return await handler.execute(state, config, context)
