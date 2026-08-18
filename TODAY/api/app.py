from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.ai.graph.orchestrator import build_orchestrator_graph
from app.ai.registry.bootstrap import register_builtin_workflows
from app.ai.runtime.services import configure_runtime_services
from app.ai.tools.bootstrap import register_builtin_toolsets
from app.api.routes_presentations import router as presentations_router
from app.api.routes_runs import router as runs_router
from app.api.routes_search_results import router as search_results_router
from app.api.routes_threads import router as threads_router
from app.api.run_service import ConversationRunService
from app.memory.application import MemoryApplication
from app.memory.checkpoint import close_checkpointer, get_checkpointer
from app.memory.facade import MemoryFacade
from app.memory.settings import default_memory_settings


def create_app(
    *,
    project_root: Path | None = None,
) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        register_builtin_workflows()
        register_builtin_toolsets()

        memory_application = MemoryApplication(
            default_memory_settings(root)
        )
        await memory_application.start()

        memory = MemoryFacade(memory_application)

        configure_runtime_services(
            memory=memory,
        )

        checkpointer = await get_checkpointer(
            memory_application,
        )

        graph = build_orchestrator_graph(
            checkpointer=checkpointer,
        )

        app.state.memory_application = memory_application
        app.state.memory = memory
        app.state.checkpointer = checkpointer
        app.state.graph = graph
        app.state.run_service = ConversationRunService(
            graph=graph,
            memory=memory,
        )

        try:
            yield
        finally:
            await close_checkpointer()
            await memory_application.stop()

    app = FastAPI(
        title="Incident Agent API",
        version="4.0.0",
        lifespan=lifespan,
    )

    app.include_router(threads_router)
    app.include_router(runs_router)
    app.include_router(search_results_router)
    app.include_router(presentations_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    return app


app = create_app()