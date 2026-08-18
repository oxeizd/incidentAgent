from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from app.memory.api.router import router
from app.memory.application import MemoryApplication
from app.memory.facade import MemoryFacade
from app.memory.settings import default_memory_settings


def create_memory_api(
    project_root: Path | None = None,
) -> FastAPI:
    """
    Standalone API boundary memory-модуля.

    Этот app не реализует auth самостоятельно. В production host application
    или gateway обязан положить AuthenticatedUser в request.state.current_user
    до вызова memory routes.
    """
    root = (project_root or Path.cwd()).resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        memory_application = MemoryApplication(
            default_memory_settings(root)
        )
        await memory_application.start()

        app.state.memory_application = memory_application
        app.state.memory = MemoryFacade(memory_application)

        try:
            yield
        finally:
            await memory_application.stop()

    app = FastAPI(
        title="Memory API",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.include_router(router)
    return app


app = create_memory_api()