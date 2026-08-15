from __future__ import annotations
from app.ai.registry.workflows import (
    RegisteredWorkflow, WorkerDependency, ArtifactDependency,
    AnyOfConstraint, HasContextKey, register_workflow,
)
from app.ai.schemas.payloads import SearchPayload, RCAPayload, EditorPayload, CreatorPayload


def register_builtin_workflows() -> None:
    register_workflow(RegisteredWorkflow(
        kind="search", entry_node="resolve_entity", payload_schema=SearchPayload,
        description="Find and resolve entities, then search incidents/assignments", icon="\U0001F50D",
    ))
    register_workflow(RegisteredWorkflow(
        kind="rca", entry_node="rca_gate", payload_schema=RCAPayload,
        clears_history_on_success=True,
        constraints=[AnyOfConstraint(
            constraints=[
                WorkerDependency(prior_kind="search"),
                HasContextKey("incident_number"),
                HasContextKey("raw_description"),
                HasContextKey("search_summary"),
            ],
            description="Нужен либо завершённый поиск, либо номер инцидента, либо описание проблемы, либо готовый search_summary (повторный анализ)",
        )],
        description="Root Cause Analysis (gate -> analyzer -> task_validator)",
        icon="\U0001F52C",
    ))
    register_workflow(RegisteredWorkflow(
        kind="editor", entry_node="apply_edit", payload_schema=EditorPayload,
        clears_history_on_success=True,
        constraints=[ArtifactDependency(artifact_kind="incident_report", min_version=0)],
        description="Edit artifact sections", icon="\u270F\uFE0F",
    ))
    register_workflow(RegisteredWorkflow(
        kind="creator", entry_node="collect_fields", payload_schema=CreatorPayload,
        description="Generate HTML presentation (standalone or from incident_report artifact)",
        icon="\U0001F5A5",
    ))