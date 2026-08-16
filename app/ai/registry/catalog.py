"""
app/ai/registry/catalog.py

Instance API foundation for Wave 2/8. It is intentionally small: no DI
framework and no global service locator. A default global registry can remain
during migration; tests/new composition roots can use isolated catalogs.
"""
from __future__ import annotations

from app.ai.registry.workflows import RegisteredWorkflow


class WorkflowCatalog:
    def __init__(self) -> None:
        self._workflows: dict[str, RegisteredWorkflow] = {}

    def register(self, workflow: RegisteredWorkflow) -> None:
        existing = self._workflows.get(workflow.kind)
        if existing is None:
            self._workflows[workflow.kind] = workflow
            return
        if existing != workflow:
            raise ValueError(f"workflow '{workflow.kind}' already registered with a different definition")

    def get(self, kind: str) -> RegisteredWorkflow | None:
        return self._workflows.get(kind)

    def values(self) -> tuple[RegisteredWorkflow, ...]:
        return tuple(self._workflows.values())
