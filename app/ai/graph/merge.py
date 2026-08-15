from __future__ import annotations
from datetime import datetime
from typing import Optional

from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState
from app.ai.schemas.artifact import SectionPatch
from app.ai.registry.workflows import WORKFLOW_REGISTRY
from app.ai.runtime.artifacts import apply_patches_to_artifact, replace_artifact_sections

SUCCESS_STATUSES = {"done", "validated", "low_confidence_stop"}


def _extract_reply_text(worker: WorkerState) -> Optional[str]:
    history = worker.get("history") or []
    for msg in reversed(history):
        content = getattr(msg, "content", None)
        if content:
            return content
    return None


def finalize_worker(state: OrchestratorState, worker: WorkerState) -> dict:
    reply_text = _extract_reply_text(worker)

    workers = dict(state["workers"])
    spec = WORKFLOW_REGISTRY.get(worker["kind"])
    is_success = worker["status"] in SUCCESS_STATUSES
    should_clear = is_success and spec is not None and spec.clears_history_on_success

    if should_clear:
        worker = {**worker, "history": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]}

    workers[worker["worker_id"]] = worker
    update: dict = {"workers": workers}

    if worker["status"] == "awaiting_user_input":
        update["pending_interrupt"] = {
            "worker_id": worker["worker_id"],
            "question": worker["history"][-1].content if worker["history"] else None,
        }
        update["focus_worker_id"] = worker["worker_id"]
    else:
        update["pending_interrupt"] = None
        update["focus_worker_id"] = None

    if reply_text:
        update["messages"] = [AIMessage(content=reply_text)]

    return update


def finalize_editor_result(state: OrchestratorState, worker: WorkerState) -> dict:
    base_update = finalize_worker(state, worker)
    if worker["status"] != "done" or not worker.get("summary_for_parent"):
        return base_update

    summary = worker["summary_for_parent"]
    artifact_id = summary.get("artifact_id")
    if not artifact_id:
        return base_update

    artifacts = dict(state["artifacts"])
    artifact = artifacts.get(artifact_id)
    if artifact is None:
        return base_update

    if summary.get("patch"):
        patch = SectionPatch(**summary["patch"])
        artifacts[artifact_id] = apply_patches_to_artifact(dict(artifact), [patch])
    elif "updated_tasks" in summary:
        artifacts[artifact_id] = replace_artifact_sections(
            dict(artifact), {"tasks": summary["updated_tasks"]},
            produced_by_worker_id=worker["worker_id"], note="Заменена системная мера",
        )
    else:
        return base_update

    return {**base_update, "artifacts": artifacts}


def suspend_focus(state: OrchestratorState, reason: str) -> dict:
    if not state["focus_worker_id"]:
        return {}
    frame = {"worker_id": state["focus_worker_id"], "reason_suspended": reason, "created_at": datetime.now().isoformat()}
    return {"plan_stack": [*state["plan_stack"], frame], "focus_worker_id": None, "pending_interrupt": None}


def defer_worker(state: OrchestratorState, worker: WorkerState, *, reason: str) -> dict:
    workers = {**state["workers"], worker["worker_id"]: worker}
    frame = {"worker_id": worker["worker_id"], "reason_suspended": reason, "created_at": datetime.now().isoformat()}
    return {"workers": workers, "plan_stack": [*state["plan_stack"], frame]}


def resume_focus(state: OrchestratorState) -> Optional[dict]:
    if not state["plan_stack"]:
        return None
    *rest, frame = state["plan_stack"]
    return {"plan_stack": rest, "focus_worker_id": frame["worker_id"]}