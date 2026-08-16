"""
app/ai/graph/orchestrator/handlers/analyze.py

Direct RCA intent: создаёт rca worker из incident_number или raw_description,
после успешного завершения применяет generic finalizer и зарегистрированный
RCA result handler (incident_report artifact). Никакой presentation/edit
логики здесь нет.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.schemas.orchestrator import OrchestratorState


async def run_direct_rca(
    state: OrchestratorState,
    config,
    context: OrchestratorContext,
    *,
    incident_number: str | None = None,
    raw_description: str | None = None,
) -> Command:
    input_context = {"payload": {}}
    if incident_number:
        input_context["incident_number"] = incident_number
    elif raw_description:
        input_context["raw_description"] = raw_description
    else:
        return Command(update={"messages": [AIMessage(content="Не понял, что именно анализировать.")]})

    outcome = await context.runner.run("rca", input_context, state, config)
    from app.ai.runtime.worker_runner import WorkerRunFailure
    if isinstance(outcome, WorkerRunFailure):
        return Command(update={"messages": [AIMessage(content=outcome.error.to_user_message())]})
    worker = outcome.worker

    reroute = await reroute_if_deviated(worker, state, config, context)
    if reroute is not None:
        return reroute

    update = finalize_for("rca")(state, worker)
    update.update(await context.artifact_handlers.apply("rca", state, worker))
    return Command(update=update)


class AnalyzeHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        return await run_direct_rca(
            state, config, context,
            incident_number=state.get("_incident_number"),
            raw_description=state.get("_raw_description"),
        )


handler = AnalyzeHandler()
