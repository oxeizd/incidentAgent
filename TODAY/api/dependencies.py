from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from app.memory.facade import MemoryFacade


def get_memory(request: Request) -> MemoryFacade:
    memory = getattr(request.app.state, "memory", None)
    if not isinstance(memory, MemoryFacade):
        raise RuntimeError("Memory facade is not initialized")
    return memory


def require_user_id(request: Request) -> str:
    """Temporary header auth until the host auth provider is connected."""
    user_id = request.headers.get("X-User-ID", "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-ID",
        )
    return user_id


def graph_config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 50,
    }


async def require_owned_thread(
    *, request: Request, thread_id: str, user_id: str
) -> MemoryFacade:
    memory = get_memory(request)
    belongs = await memory.thread_belongs_to_user(
        user_id=user_id,
        thread_id=thread_id,
    )
    if not belongs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found or not available to this user",
        )
    return memory
