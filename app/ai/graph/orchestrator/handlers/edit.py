"""app/ai/graph/orchestrator/handlers/edit.py — isolated edit_report flow."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.ai.graph.orchestrator.artifacts.incident_report import format_gate_result_block
from app.ai.graph.orchestrator.context import OrchestratorContext
from app.ai.graph.orchestrator.deviation import reroute_if_deviated
from app.ai.graph.orchestrator.finalizers import finalize_for
from app.ai.graph.orchestrator.models import EditRequest
from app.ai.graph.orchestrator.prompts import EDIT_FALLBACK_PROMPT
from app.ai.prompts.registry import get_prompt
from app.ai.runtime.worker_runner import WorkerRunFailure
from app.ai.schemas.orchestrator import OrchestratorState


class EditHandler:
    async def execute(self, state: OrchestratorState, config, context: OrchestratorContext) -> Command:
        artifact_id = state["current_artifact_id"]
        if not artifact_id or artifact_id not in state["artifacts"]:
            return Command(update={"messages": [AIMessage(content="Пока нечего редактировать — сначала нужен готовый отчёт.")]})

        artifact = state["artifacts"][artifact_id]
        sections = artifact["versions"][artifact["current_version"]]["sections"]
        editable_sections = [key for key in sections if not key.startswith("_")]

        extra_context = {"available_sections": editable_sections}
        if "tasks" in sections:
            extra_context["tasks"] = [
                {"number": index + 1, "title": task.get("title")}
                for index, task in enumerate(sections["tasks"])
            ]

        system = context.llm.build_system_message(
            role_instruction=get_prompt("editor_intent", fallback=EDIT_FALLBACK_PROMPT),
            extra_context=extra_context,
            output_contract="JSON по схеме EditRequest.",
        )
        edit_request = await context.llm.ainvoke_structured(
            [system, HumanMessage(content=state["messages"][-1].content)],
            EditRequest,
            worker_kind="editor",
        )
        task_index = edit_request.task_number - 1 if edit_request.task_number else None

        input_context = {
            "payload": {
                "target_artifact_id": artifact_id,
                "target_section": edit_request.target_section,
                "instruction": edit_request.instruction,
                "task_index": task_index,
            },
            "artifact_id": artifact_id,
            "current_section_value": sections.get(edit_request.target_section),
        }
        if edit_request.target_section == "tasks":
            rca_input = sections.get("_rca_input") or {}
            input_context["current_tasks"] = sections.get("tasks", [])
            input_context["rca_context_block"] = format_gate_result_block(rca_input.get("gate_result", {}))

        outcome = await context.runner.run("editor", input_context, state, config)
        if isinstance(outcome, WorkerRunFailure):
            return Command(update={"messages": [AIMessage(content=outcome.error.to_user_message())]})
        worker = outcome.worker

        reroute = await reroute_if_deviated(worker, state, config, context)
        if reroute is not None:
            return reroute
        return Command(update=finalize_for("editor")(state, worker))


handler = EditHandler()
