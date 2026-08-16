"""Declarative editor workflow module (Wave 8)."""
from __future__ import annotations

from app.ai.graph.build import build_editor_graph
from app.ai.registry.workflows import ArtifactDependency, RegisteredWorkflow
from app.ai.schemas.payloads import EditorPayload
from app.ai.workflows.modules import WorkflowModule

EDITOR_WORKFLOW = WorkflowModule(
    spec=RegisteredWorkflow(
        kind="editor",
        entry_node="apply_edit",
        payload_schema=EditorPayload,
        clears_history_on_success=True,
        constraints=(ArtifactDependency(artifact_kind="incident_report", min_version=0),),
        description="Edit artifact sections",
        icon="✏️",
    ),
    build_graph=build_editor_graph,
)
