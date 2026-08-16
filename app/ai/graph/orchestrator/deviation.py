"""
app/ai/graph/orchestrator/deviation.py

Общая для ВСЕХ handlers логика: если воркер вернулся со status="deviated"
(пользователь ушёл от заданного вопроса — см. app/ai/runtime/node_kit.py:
UserDeviated / check_not_deviation), этот воркер откладывается в plan_stack
(defer_worker из app/ai/graph/merge.py), новая реплика реклассифицируется, и
получившийся intent диспетчеризуется заново — без единого специального
знания о конкретном domain workflow. Раньше это была функция
_reroute_if_deviated(), продублированная по сигнатуре в каждом из ~7 мест
внутри старого orchestrator.py; теперь один общий вызов.
"""
from __future__ import annotations
from typing import Optional

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState
from app.ai.graph.merge import defer_worker
from app.ai.graph.orchestrator.context import OrchestratorContext


async def reroute_if_deviated(
    result_worker: Optional[WorkerState], state: OrchestratorState, config, context: OrchestratorContext,
) -> Optional[Command]:
    if not result_worker or result_worker.get("status") != "deviated":
        return None

    deviation_text = result_worker["error"]["message"]
    defer_update = defer_worker(state, result_worker, reason=f"отклонился от вопроса: {deviation_text!r}")

    rerouted_state = {**state, **defer_update, "messages": [*state["messages"], HumanMessage(content=deviation_text)]}
    decision = await context.classifier.classify(rerouted_state)
    rerouted_state = {**rerouted_state, **decision.to_state_update()}

    result = await context.dispatcher.dispatch(rerouted_state, config, context)

    merged_update = dict(result.update)
    merged_update["workers"] = {**defer_update["workers"], **merged_update.get("workers", {})}
    merged_update.setdefault("plan_stack", defer_update["plan_stack"])
    merged_update["messages"] = [HumanMessage(content=deviation_text), *merged_update.get("messages", [])]

    return Command(update=merged_update)
