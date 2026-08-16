"""
app/ai/graph/orchestrator/handlers/search.py

Handles both new_search and search_then_analyze. The second flow stays
explicitly sequential: RCA depends on the exact SearchPage-based summary from
the preceding search worker, therefore it MUST NOT use WorkerRunner.run_many().
"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.merge import suspend_focus
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.runtime.worker_runner import WorkerRunFailure
from app.ai.schemas.orchestrator import OrchestratorState


class SearchHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        intent = state["intent"]
        suspend_update = suspend_focus(state, reason=f"user switched to intent={intent}") if state["focus_worker_id"] else {}

        raw_query = state.get("_resolved_query") or state["messages"][-1].content
        outcome = await context.runner.run("search", {"payload": {"raw_query": raw_query}}, state, config)
        if isinstance(outcome, WorkerRunFailure):
            return Command(update={**suspend_update, "messages": [AIMessage(content=outcome.error.to_user_message())]})
        search_worker = outcome.worker

        reroute = await reroute_if_deviated(search_worker, state, config, context)
        if reroute is not None:
            return Command(update={**suspend_update, **reroute.update})

        update = finalize_for("search")(state, search_worker)
        if intent != "search_then_analyze" or search_worker["status"] != "done":
            return Command(update={**suspend_update, **update})

        merged_state = {**state, **update}
        rca_context = {
            "payload": {},
            "parent_worker_id": search_worker["worker_id"],
            "search_summary": search_worker["summary_for_parent"],
        }
        rca_outcome = await context.runner.run("rca", rca_context, merged_state, config)
        if isinstance(rca_outcome, WorkerRunFailure):
            update.setdefault("messages", []).append(AIMessage(content=rca_outcome.error.to_user_message()))
            return Command(update={**suspend_update, **update})
        rca_worker = rca_outcome.worker

        rca_reroute = await reroute_if_deviated(rca_worker, merged_state, config, context)
        if rca_reroute is not None:
            return Command(update={**suspend_update, **update, **rca_reroute.update})

        rca_update = finalize_for("rca")(merged_state, rca_worker)
        update["workers"] = {**update["workers"], **rca_update["workers"]}
        update["pending_interrupt"] = rca_update["pending_interrupt"]
        update["focus_worker_id"] = rca_update["focus_worker_id"]
        if "messages" in rca_update:
            update["messages"] = [*update.get("messages", []), *rca_update["messages"]]

        artifact_update = await context.artifact_handlers.apply("rca", merged_state, rca_worker)
        update.update(artifact_update)
        return Command(update={**suspend_update, **update})


handler = SearchHandler()
