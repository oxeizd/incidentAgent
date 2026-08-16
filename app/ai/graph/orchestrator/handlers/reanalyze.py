"""app/ai/graph/orchestrator/handlers/reanalyze.py — repeat RCA with new evidence."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.orchestrator.artifacts.incident_report import gate_result_from_payload
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.runtime.artifacts import replace_artifact_sections
from app.ai.runtime.worker_runner import WorkerRunFailure
from app.ai.schemas.orchestrator import OrchestratorState


class ReanalyzeHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        artifact_id = state["current_artifact_id"]
        if not artifact_id or artifact_id not in state["artifacts"]:
            return Command(update={"messages": [AIMessage(content="Сначала нужен готовый RCA-отчёт, чтобы уточнить его новыми фактами.")]})

        artifact = state["artifacts"][artifact_id]
        current_sections = artifact["versions"][artifact["current_version"]]["sections"]
        rca_input = current_sections.get("_rca_input")
        if not rca_input:
            return Command(update={"messages": [AIMessage(content="Не нашёл исходные данные анализа для повторного прогона.")]})

        evidence_text = state.get("_evidence") or state["messages"][-1].content
        new_input_context = {
            "incident_number": rca_input.get("incident_number"),
            "raw_description": rca_input.get("raw_description"),
            "search_summary": rca_input.get("search_summary"),
            "evidence": [*rca_input.get("evidence", []), evidence_text],
        }
        outcome = await context.runner.run("rca", {"payload": {}, **new_input_context}, state, config)
        if isinstance(outcome, WorkerRunFailure):
            return Command(update={"messages": [AIMessage(content=outcome.error.to_user_message())]})
        worker = outcome.worker

        reroute = await reroute_if_deviated(worker, state, config, context)
        if reroute is not None:
            return reroute

        update = finalize_for("rca")(state, worker)
        if worker["status"] == "done":
            summary = worker.get("summary_for_parent") or {}
            if "analysis" in summary and "tasks" in summary:
                new_rca_input = {
                    **new_input_context,
                    "gate_result": gate_result_from_payload(worker.get("payload") or {}),
                }
                updated_artifact = replace_artifact_sections(
                    dict(artifact),
                    {"analysis": summary["analysis"], "tasks": summary["tasks"], "_rca_input": new_rca_input},
                    produced_by_worker_id=worker["worker_id"],
                    note=f"Повторный анализ с учётом нового факта: {evidence_text}",
                )
                update["artifacts"] = {**state["artifacts"], artifact_id: updated_artifact}
                update["current_artifact_id"] = artifact_id
        return Command(update=update)


handler = ReanalyzeHandler()
