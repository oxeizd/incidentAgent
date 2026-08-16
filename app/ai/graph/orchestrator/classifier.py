"""
app/ai/graph/orchestrator/classifier.py

Wave 4: классификация намерения вынесена из orchestrator.py в отдельный,
unit-тестируемый компонент — можно подменить llm в тестах (fake LLM),
не импортируя весь orchestrator-модуль и не поднимая граф целиком.
"""
from __future__ import annotations

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.graph.orchestrator.models import IntentClassification, RoutingDecision
from app.ai.graph.orchestrator.prompts import SUPERVISOR_FALLBACK_PROMPT
from app.ai.prompts.registry import get_prompt
from app.services.llm import llm_client as _default_llm_client


class IntentClassifier:
    def __init__(self, llm=_default_llm_client):
        self._llm = llm

    async def classify(self, state: OrchestratorState) -> RoutingDecision:
        context_hint = ""
        if state["pending_interrupt"] and state["focus_worker_id"]:
            context_hint = (
                f"Агент только что спросил: {state['pending_interrupt']['question']!r}. "
                "Если реплика — ответ на этот вопрос, интент resume_previous."
            )

        recent = state["messages"][-10:] if len(state["messages"]) > 10 else list(state["messages"])
        messages = [
            self._llm.build_system_message(
                role_instruction=get_prompt("supervisor", fallback=SUPERVISOR_FALLBACK_PROMPT),
                extra_context={"hint": context_hint} if context_hint else None,
                output_contract="JSON по схеме IntentClassification.",
            ),
        ]
        messages.extend(recent)

        result = await self._llm.ainvoke_structured(messages, IntentClassification, worker_kind="supervisor")
        return RoutingDecision.from_classification(result)


async def classify_intent(state: OrchestratorState) -> dict:
    """
    Graph node adapter — сохраняет прежний контракт state-update ключей
    (см. RoutingDecision.to_state_update), совместимый с уже записанными
    checkpoints до этой миграции.
    """
    decision = await IntentClassifier().classify(state)
    return decision.to_state_update()
