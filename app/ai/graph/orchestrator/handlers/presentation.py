"""app/ai/graph/orchestrator/handlers/presentation.py — create_presentation flow."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.runtime.worker_runner import WorkerRunFailure
from app.ai.schemas.orchestrator import OrchestratorState
from app.memory.repository.incidents import get_incident_by_number


class PresentationHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        artifact_id = state["current_artifact_id"]
        if artifact_id and artifact_id in state["artifacts"]:
            artifact = state["artifacts"][artifact_id]
            sections = artifact["versions"][artifact["current_version"]]["sections"]
            input_context = {"payload": {}, "artifact_id": artifact_id, "artifact_sections": sections}
        else:
            if state.get("_incident_number"):
                incident = await get_incident_by_number(state["_incident_number"])
                source_text = str(incident) if incident else f"Инцидент {state['_incident_number']} не найден в базе."
            elif state.get("_raw_description"):
                source_text = state["_raw_description"]
            else:
                source_text = state["messages"][-1].content
            input_context = {"payload": {}, "source_text": source_text}

        outcome = await context.runner.run("creator", input_context, state, config)
        if isinstance(outcome, WorkerRunFailure):
            return Command(update={"messages": [AIMessage(content=outcome.error.to_user_message())]})
        worker = outcome.worker

        reroute = await reroute_if_deviated(worker, state, config, context)
        if reroute is not None:
            return reroute

        update = finalize_for("creator")(state, worker)
        artifact_update = await context.artifact_handlers.apply("creator", state, worker)
        if artifact_update:
            update = {**update, **artifact_update}
            if "messages" in update and "messages" in artifact_update:
                update["messages"] = [*finalize_for("creator")(state, worker).get("messages", []), *artifact_update["messages"]]
        return Command(update=update)


handler = PresentationHandler()
