"""Declarative creator workflow module (Wave 8)."""
from __future__ import annotations

from app.ai.graph.build import build_creator_graph
from app.ai.registry.workflows import RegisteredWorkflow
from app.ai.schemas.payloads import CreatorPayload
from app.ai.workflows.modules import WorkflowModule

CREATOR_WORKFLOW = WorkflowModule(
    spec=RegisteredWorkflow(
        kind="creator",
        entry_node="collect_fields",
        payload_schema=CreatorPayload,
        description="Generate HTML presentation (standalone or from incident_report artifact)",
        icon="🖥",
    ),
    build_graph=build_creator_graph,
)
