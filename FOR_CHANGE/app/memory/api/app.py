from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from memory.api.dependencies import AuthenticatedUser
from memory.api.router import router
from memory.application import MemoryApplication
from memory.facade import MemoryFacade
from memory.settings import default_memory_settings


class DevelopmentAuthMiddleware(BaseHTTPMiddleware):
    """
    Local-only auth stub.

    Every local request becomes one fixed user. Never deploy this middleware
    to a public or shared environment.
    """

    async def dispatch(self, request, call_next):
        request.state.current_user = AuthenticatedUser(
            id="local-dev-user",
            permissions=frozenset({"memory:admin"}),
        )
        return await call_next(request)


def create_memory_api(project_root: Path | None = None) -> FastAPI:
    root = project_root or Path.cwd()

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
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(DevelopmentAuthMiddleware)
    app.include_router(router)

    return app


app = create_memory_api()