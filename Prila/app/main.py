from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.ai.graph.conversation_graph import (
    build_conversation_graph,
)
from app.ai.registry.bootstrap import (
    register_builtin_workflows,
)
from app.ai.runtime.services import configure_runtime_services
from app.api.auth import create_user_resolver
from app.api.conversation_service import ConversationService
from app.api.router import router as api_router
from app.config import settings, setup_logging
from app.memory.application import MemoryApplication
from app.memory.checkpoint import (
    close_checkpointer,
    get_checkpointer,
)
from app.memory.facade import MemoryFacade
from app.memory.seed_loader import load_seed_data
from app.memory.settings import default_memory_settings
from app.observability.phoenix import (
    start_phoenix,
    stop_phoenix_and_save_traces,
)


logger = logging.getLogger(__name__)


def create_application(
    *,
    project_root: Path | None = None,
) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()

    setup_logging()

    @asynccontextmanager
    async def lifespan(
        app: FastAPI,
    ) -> AsyncIterator[None]:
        logger.info(
            "App startup: initializing conversation graph and memory"
        )

        register_builtin_workflows()

        memory_application = MemoryApplication(
            default_memory_settings(root)
        )
        await memory_application.start()

        memory = MemoryFacade(memory_application)

        if settings.LOAD_DATA:
            logger.info("Loading seed data")
            await load_seed_data(
                memory_application,
                root,
            )
        else:
            logger.info(
                "Seed data loading disabled (LOAD_DATA=false)"
            )

        configure_runtime_services(memory=memory)

        checkpointer = await get_checkpointer(
            memory_application,
        )

        graph = build_conversation_graph(
            checkpointer=checkpointer,
        )

        app.state.memory_application = memory_application
        app.state.memory = memory
        app.state.checkpointer = checkpointer
        app.state.graph = graph
        app.state.user_resolver = create_user_resolver()
        app.state.conversation_service = ConversationService(
            graph=graph,
            memory=memory,
        )

        if settings.PHOENIX_ENABLED:
            start_phoenix()

        try:
            yield
        finally:
            logger.info("App shutdown: stopping services")

            if settings.PHOENIX_ENABLED:
                await stop_phoenix_and_save_traces()

            await close_checkpointer()
            await memory_application.stop()

    app = FastAPI(
        title="Incident Agent",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.include_router(api_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    frontend_directory = root / "frontend" / "dist"

    if frontend_directory.exists():
        app.mount(
            "/",
            StaticFiles(
                directory=str(frontend_directory),
                html=True,
            ),
            name="frontend",
        )

    return app


app = create_application()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )