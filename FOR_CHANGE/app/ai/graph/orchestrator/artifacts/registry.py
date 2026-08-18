from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState
from memory.facade import MemoryFacade


class WorkerResultHandler(Protocol):
    async def apply(
        self,
        state: OrchestratorState,
        worker: WorkerState,
    ) -> dict[str, Any]:
        ...


class ArtifactHandlerRegistry:
    """
    Immutable registry of terminal worker-result handlers.

    The registry is composed once during application startup and only read by
    the orchestration graph during execution.
    """

    def __init__(
        self,
        handlers: Mapping[str, WorkerResultHandler],
    ) -> None:
        self._handlers = dict(handlers)

    async def apply(
        self,
        worker_kind: str,
        state: OrchestratorState,
        worker: WorkerState,
    ) -> dict[str, Any]:
        handler = self._handlers.get(worker_kind)

        if handler is None:
            return {}

        return await handler.apply(state, worker)


def build_default_artifact_handlers(
    *,
    memory: MemoryFacade,
) -> ArtifactHandlerRegistry:
    """
    Compose built-in handlers with explicit application dependencies.
    """
    from app.ai.graph.orchestrator.artifacts.incident_report import (
        handler as incident_report_handler,
    )
    from app.ai.graph.orchestrator.artifacts.presentation import (
        PresentationResultHandler,
    )

    return ArtifactHandlerRegistry(
        {
            "rca": incident_report_handler,
            "creator": PresentationResultHandler(memory),
        }
    )