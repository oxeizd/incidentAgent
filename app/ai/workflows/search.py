"""Declarative search workflow module (Wave 8)."""
from __future__ import annotations

from app.ai.graph.build import build_search_graph
from app.ai.registry.workflows import RegisteredWorkflow
from app.ai.schemas.payloads import SearchPayload
from app.ai.workflows.modules import WorkflowModule

SEARCH_WORKFLOW = WorkflowModule(
    spec=RegisteredWorkflow(
        kind="search",
        entry_node="resolve_entity",
        payload_schema=SearchPayload,
        description="Find and resolve entities, then search incidents/assignments",
        icon="🔍",
    ),
    build_graph=build_search_graph,
)
