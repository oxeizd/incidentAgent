"""app/ai/graph/orchestrator/handlers/resume.py — resume deferred worker after deviation."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.merge import resume_focus
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.runtime.worker_runner import WorkerRunFailure
from app.ai.schemas.orchestrator import OrchestratorState


class ResumeHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        update = resume_focus(state)
        if update is None:
            return Command(update={"messages": [AIMessage(content="Не нашёл, к чему возвращаться.")]})

        worker_id = update["focus_worker_id"]
        worker = state["workers"].get(worker_id)
        if worker is None:
            return Command(update={**update, "messages": [AIMessage(content="Не нашёл сохранённый прогресс по этой задаче.")]})
        if worker["status"] != "deviated":
            return Command(update=update)

        kind = worker["kind"]
        input_context = {**worker["input_context"], "payload": worker["payload"]}
        outcome = await context.runner.run(kind, input_context, state, config)
        if isinstance(outcome, WorkerRunFailure):
            return Command(update={**update, "messages": [AIMessage(content=outcome.error.to_user_message())]})
        result_worker = outcome.worker

        reroute = await reroute_if_deviated(result_worker, state, config, context)
        if reroute is not None:
            return Command(update={**update, **reroute.update})

        final_update = {**update, **finalize_for(kind)(state, result_worker)}
        artifact_update = await context.artifact_handlers.apply(kind, state, result_worker)
        if artifact_update:
            final_update = {**final_update, **artifact_update}
            if "messages" in artifact_update:
                final_update["messages"] = [
                    *finalize_for(kind)(state, result_worker).get("messages", []),
                    *artifact_update["messages"],
                ]
        return Command(update=final_update)


handler = ResumeHandler()
