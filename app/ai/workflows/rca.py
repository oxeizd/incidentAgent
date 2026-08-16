"""Declarative RCA workflow module (Wave 8)."""
from __future__ import annotations

from app.ai.graph.build import build_rca_graph
from app.ai.registry.workflows import AnyOfConstraint, HasContextKey, RegisteredWorkflow, WorkerDependency
from app.ai.schemas.payloads import RCAPayload
from app.ai.workflows.modules import WorkflowModule

RCA_WORKFLOW = WorkflowModule(
    spec=RegisteredWorkflow(
        kind="rca",
        entry_node="rca_gate",
        payload_schema=RCAPayload,
        clears_history_on_success=True,
        constraints=(AnyOfConstraint(
            constraints=[
                WorkerDependency(prior_kind="search"),
                HasContextKey("incident_number"),
                HasContextKey("raw_description"),
                HasContextKey("search_summary"),
            ],
            description=(
                "Нужен либо завершённый поиск, либо номер инцидента, либо описание проблемы, "
                "либо готовый search_summary (повторный анализ)"
            ),
        ),),
        description="Root Cause Analysis (gate -> analyzer -> task_validator)",
        icon="🔬",
    ),
    build_graph=build_rca_graph,
)
