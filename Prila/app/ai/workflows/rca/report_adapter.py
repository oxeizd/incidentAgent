from __future__ import annotations

from typing import Any

from app.ai.runtime.artifacts import create_artifact
from app.ai.schemas.conversation import (
    IncidentReportRef,
    utc_now_iso,
)
from app.ai.workflows.rca.contracts import (
    RCAGateDecision,
    RCAInput,
    RCAReportDraft,
    RCAValidationResult,
    ValidatedTask,
)


def build_report_sections(
    *,
    rca_input: RCAInput,
    gate: RCAGateDecision,
    draft: RCAReportDraft,
    validation: RCAValidationResult,
) -> dict[str, Any]:
    """
    Собирает sections первой версии incident_report.

    `analysis` и `tasks` остаются в совместимом формате для существующего
    editor/presentation/UI. `_rca` — новый полный structured snapshot и
    единственный источник для reanalyze/presentation следующей архитектуры.
    """
    accepted_tasks = validation.accepted_tasks

    return {
        # Совместимость с существующим renderer/editor.
        "analysis": draft.analysis,
        "tasks": [
            _task_to_legacy_section(task)
            for task in accepted_tasks
        ],

        # Полный воспроизводимый RCA context новой системы.
        "_rca": {
            "input": rca_input.model_dump(mode="json"),
            "gate": gate.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
            "accepted_task_count": len(accepted_tasks),
            "rejected_task_count": len(
                validation.rejected_tasks
            ),
        },

        # Короткий domain-friendly snapshot для presentation/editor.
        "summary": draft.summary,
        "affected_systems": draft.affected_systems,
        "symptoms": draft.symptoms,
        "impact": draft.impact,
        "timeline": draft.timeline,
        "facts": [
            item.model_dump(mode="json")
            for item in draft.facts
        ],
        "root_cause": draft.root_cause,
        "root_cause_kind": draft.root_cause_kind,
        "causal_chain": draft.causal_chain,
        "contributing_factors": draft.contributing_factors,
        "applied_measures": draft.applied_measures,
        "corrective_actions": [
            task.model_dump(mode="json")
            for task in draft.corrective_actions
        ],
        "preventive_actions": [
            task.model_dump(mode="json")
            for task in draft.preventive_actions
        ],
        "open_questions": draft.open_questions,
        "limitations": draft.limitations,
        "confidence": draft.confidence,
        "confidence_reason": draft.confidence_reason,
    }


def create_incident_report(
    *,
    task_id: str,
    rca_input: RCAInput,
    gate: RCAGateDecision,
    draft: RCAReportDraft,
    validation: RCAValidationResult,
) -> tuple[dict[str, Any], IncidentReportRef]:
    """
    Создаёт первую draft-версию RCA report artifact.

    Artifact ID генерируется здесь, а не LLM. Пока artifact живёт в root
    ConversationState; внешний persistent report repository можно подключить
    позднее, не меняя workflow contracts.
    """
    artifact_id = f"incident-report-{task_id}"

    artifact = create_artifact(
        artifact_id=artifact_id,
        kind="incident_report",
        initial_sections=build_report_sections(
            rca_input=rca_input,
            gate=gate,
            draft=draft,
            validation=validation,
        ),
        created_by_worker_id=task_id,
        status="draft",
    )

    incident_number = (
        rca_input.incident_ref.number
        if rca_input.incident_ref is not None
        else None
    )

    ref = IncidentReportRef(
        id=artifact_id,
        label=(
            f"RCA-справка по {incident_number}"
            if incident_number
            else "RCA-справка"
        ),
    )

    return artifact, ref


def _task_to_legacy_section(
    task: ValidatedTask,
) -> dict[str, Any]:
    """
    Приводит новую validated measure к текущему compatible tasks section.

    Не теряем validation fields: editor и presentation могут показать
    статус NEW/PARTIAL/DUPLICATE и найденный похожий assignment.
    """
    return {
        "title": task.title,
        "description": task.description,
        "addresses": task.addresses,
        "type": task.type,
        "priority": task.priority,
        "expected_result": task.expected_result,
        "validation_status": task.validation_status,
        "validation_reason": task.validation_reason,
        "most_similar_assignment": (
            task.most_similar_assignment
        ),
    }