"""
app/ai/graph/orchestrator/artifacts/registry.py

Wave 6: замена словаря _ARTIFACT_HOOKS внутри старого orchestrator.py.
Каждый workflow владеет своим result handler ЛОКАЛЬНО (см.
app/ai/graph/orchestrator/artifacts/incident_report.py,
app/ai/graph/orchestrator/artifacts/presentation.py) — intent handlers
(app/ai/graph/orchestrator/handlers/*) вызывают context.artifact_handlers.apply(kind, ...)
без единого `if kind == "rca"` / `if kind == "creator"`.
"""
from __future__ import annotations
from typing import Mapping, Optional, Protocol

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState


class WorkerResultHandler(Protocol):
    async def apply(self, state: OrchestratorState, worker: WorkerState) -> dict: ...


class ArtifactHandlerRegistry:
    def __init__(self, handlers: Optional[Mapping[str, WorkerResultHandler]] = None):
        self._handlers: dict[str, WorkerResultHandler] = dict(handlers or {})

    def register(self, kind: str, handler: WorkerResultHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> Optional[WorkerResultHandler]:
        return self._handlers.get(kind)

    async def apply(self, kind: str, state: OrchestratorState, worker: WorkerState) -> dict:
        handler = self._handlers.get(kind)
        if handler is None:
            return {}
        return await handler.apply(state, worker)


def build_default_artifact_handlers() -> ArtifactHandlerRegistry:
    from app.ai.graph.orchestrator.artifacts.incident_report import handler as incident_report_handler
    from app.ai.graph.orchestrator.artifacts.presentation import handler as presentation_handler

    return ArtifactHandlerRegistry({
        "rca": incident_report_handler,
        "creator": presentation_handler,
    })
