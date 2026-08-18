from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage

from app.ai.registry.workflows import WORKFLOW_REGISTRY
from app.ai.runtime.artifacts import (
    apply_patches_to_artifact,
    create_artifact,
    replace_artifact_sections,
)
from app.ai.schemas.artifact import SectionPatch
from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState


SUCCESS_STATUSES = frozenset(
    {
        "done",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_worker_reply(
    worker: WorkerState,
) -> str | None:
    """
    Последнее непустое AI message content worker-а.
    """
    for message in reversed(worker.get("history") or []):
        content = getattr(message, "content", None)

        if isinstance(content, str) and content.strip():
            return content.strip()

    return None


def finalize_worker(
    state: OrchestratorState,
    worker: WorkerState,
) -> dict[str, Any]:
    """
    Базовый merge результата worker-а в root state.

    Worker всегда сохраняется в `workers`, даже если завершился неуспешно:
    это нужно для audit/resume/diagnostics.
    """
    workers = {
        **state["workers"],
        worker["worker_id"]: worker,
    }

    update: dict[str, Any] = {
        "workers": workers,
    }

    reply = extract_worker_reply(worker)

    if worker["status"] == "awaiting_user_input":
        update["focus_worker_id"] = worker["worker_id"]
        update["pending_interrupt"] = {
            "worker_id": worker["worker_id"],
            "question": reply or "Уточните, пожалуйста, данные.",
            "interaction_type": "question",
        }
    else:
        update["focus_worker_id"] = None
        update["pending_interrupt"] = None

    if worker["status"] in SUCCESS_STATUSES:
        completed = list(state.get("completed_workers") or [])

        if worker["worker_id"] not in completed:
            completed.append(worker["worker_id"])

        update["completed_workers"] = completed

        spec = WORKFLOW_REGISTRY.get(worker["kind"])

        if spec and spec.clears_history_on_success:
            # Worker history оставляем в state для audit/replay. Флаг больше
            # не очищает данные физически: иначе теряем user-visible final
            # answer при последующем state inspection.
            pass

    if reply:
        update["messages"] = [
            AIMessage(content=reply),
        ]

    return update


def create_rca_report_update(
    state: OrchestratorState,
    worker: WorkerState,
) -> dict[str, Any]:
    """
    Создаёт новый incident_report artifact после первичного RCA.
    """
    if worker["status"] != "done":
        return {}

    summary = worker.get("summary_for_parent") or {}

    if (
        not isinstance(summary.get("analysis"), str)
        or not isinstance(summary.get("tasks"), list)
    ):
        return {}

    input_context = worker["input_context"]
    payload = worker["payload"]

    rca_input = {
        "incident_number": input_context.get("incident_number"),
        "raw_description": input_context.get("raw_description"),
        "search_summary": input_context.get("search_summary"),
        "evidence": list(input_context.get("evidence") or []),
        "gate_result": {
            "root_cause_statement": payload.get(
                "root_cause_statement"
            ),
            "causal_chain": payload.get("causal_chain") or [],
            "impact": payload.get("impact") or [],
            "symptoms": payload.get("symptoms") or [],
            "mitigation": payload.get("mitigation") or [],
            "incident_summary": payload.get(
                "incident_summary",
                "",
            ),
        },
    }

    artifact_id = f"incident_report-{worker['worker_id']}"

    report = create_artifact(
        artifact_id=artifact_id,
        kind="incident_report",
        initial_sections={
            "analysis": summary["analysis"],
            "tasks": summary["tasks"],
            "_rca_input": rca_input,
        },
        created_by_worker_id=worker["worker_id"],
    )

    return {
        "artifacts": {
            **state["artifacts"],
            artifact_id: report,
        },
        "current_artifact_id": artifact_id,
    }


def replace_rca_report_update(
    state: OrchestratorState,
    worker: WorkerState,
    *,
    artifact_id: str,
    evidence_text: str,
) -> dict[str, Any]:
    """
    Версионирует существующий report после reanalyze_report.

    Исходная версия остаётся неизменной. В новую попадают analysis, tasks
    и полный воспроизводимый _rca_input с добавленным evidence.
    """
    if worker["status"] != "done":
        return {}

    artifact = state["artifacts"].get(artifact_id)

    if artifact is None or artifact["kind"] != "incident_report":
        return {}

    summary = worker.get("summary_for_parent") or {}

    if (
        not isinstance(summary.get("analysis"), str)
        or not isinstance(summary.get("tasks"), list)
    ):
        return {}

    old_sections = artifact["versions"][
        artifact["current_version"]
    ]["sections"]

    old_rca_input = old_sections.get("_rca_input") or {}
    input_context = worker["input_context"]
    payload = worker["payload"]

    new_rca_input = {
        "incident_number": input_context.get(
            "incident_number"
        ) or old_rca_input.get("incident_number"),
        "raw_description": input_context.get(
            "raw_description"
        ) or old_rca_input.get("raw_description"),
        "search_summary": input_context.get(
            "search_summary"
        ) or old_rca_input.get("search_summary"),
        "evidence": [
            *list(old_rca_input.get("evidence") or []),
            evidence_text,
        ],
        "gate_result": {
            "root_cause_statement": payload.get(
                "root_cause_statement"
            ),
            "causal_chain": payload.get("causal_chain") or [],
            "impact": payload.get("impact") or [],
            "symptoms": payload.get("symptoms") or [],
            "mitigation": payload.get("mitigation") or [],
            "incident_summary": payload.get(
                "incident_summary",
                "",
            ),
        },
    }

    updated = replace_artifact_sections(
        artifact,
        {
            "analysis": summary["analysis"],
            "tasks": summary["tasks"],
            "_rca_input": new_rca_input,
        },
        produced_by_worker_id=worker["worker_id"],
        note=(
            "Повторный RCA с учётом нового факта: "
            f"{evidence_text}"
        ),
    )

    return {
        "artifacts": {
            **state["artifacts"],
            artifact_id: updated,
        },
        "current_artifact_id": artifact_id,
    }


def finalize_editor_result(
    state: OrchestratorState,
    worker: WorkerState,
) -> dict[str, Any]:
    """
    Базовый finalize + versioned patch текущего incident_report.
    """
    update = finalize_worker(state, worker)

    if worker["status"] != "done":
        return update

    summary = worker.get("summary_for_parent") or {}
    artifact_id = summary.get("artifact_id")

    if not isinstance(artifact_id, str):
        return update

    artifact = state["artifacts"].get(artifact_id)

    if artifact is None or artifact["kind"] != "incident_report":
        return update

    if isinstance(summary.get("patch"), dict):
        patch = SectionPatch.model_validate(summary["patch"])

        updated = apply_patches_to_artifact(
            artifact,
            [patch],
            note=patch.note or "Изменение RCA-отчёта",
        )
    elif isinstance(summary.get("updated_tasks"), list):
        updated = replace_artifact_sections(
            artifact,
            {
                "tasks": summary["updated_tasks"],
            },
            produced_by_worker_id=worker["worker_id"],
            note="Заменена системная мера",
        )
    else:
        return update

    update["artifacts"] = {
        **state["artifacts"],
        artifact_id: updated,
    }
    update["current_artifact_id"] = artifact_id

    return update


def suspend_focus(
    state: OrchestratorState,
    *,
    reason: str,
) -> dict[str, Any]:
    """
    Откладывает активный worker, когда пользователь начал новую задачу.
    """
    focus_worker_id = state.get("focus_worker_id")

    if not focus_worker_id:
        return {}

    frame = {
        "worker_id": focus_worker_id,
        "reason_suspended": reason,
        "created_at": utc_now_iso(),
    }

    return {
        "plan_stack": [
            *state["plan_stack"],
            frame,
        ],
        "focus_worker_id": None,
        "pending_interrupt": None,
    }


def defer_worker(
    state: OrchestratorState,
    worker: WorkerState,
    *,
    reason: str,
) -> dict[str, Any]:
    """
    Сохраняет deviated worker и добавляет его в stack возврата.
    """
    frame = {
        "worker_id": worker["worker_id"],
        "reason_suspended": reason,
        "created_at": utc_now_iso(),
    }

    return {
        "workers": {
            **state["workers"],
            worker["worker_id"]: worker,
        },
        "plan_stack": [
            *state["plan_stack"],
            frame,
        ],
    }


def resume_focus(
    state: OrchestratorState,
) -> dict[str, Any] | None:
    """
    Возвращает последний отложенный worker обратно в focus.
    """
    if not state["plan_stack"]:
        return None

    *remaining, frame = state["plan_stack"]

    return {
        "plan_stack": remaining,
        "focus_worker_id": frame["worker_id"],
        "pending_interrupt": None,
    }