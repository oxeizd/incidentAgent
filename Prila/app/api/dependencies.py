from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.auth import CurrentUser, UserResolver
from app.memory.facade import MemoryFacade


def get_memory(
    request: Request,
) -> MemoryFacade:
    memory = getattr(request.app.state, "memory", None)

    if not isinstance(memory, MemoryFacade):
        raise RuntimeError(
            "Memory facade is not initialized"
        )

    return memory


def get_current_user(
    request: Request,
) -> CurrentUser:
    resolver = getattr(
        request.app.state,
        "user_resolver",
        None,
    )

    if not isinstance(resolver, UserResolver):
        raise RuntimeError(
            "User resolver is not initialized"
        )

    return resolver.resolve(request)


CurrentUserDependency = Annotated[
    CurrentUser,
    Depends(get_current_user),
]

MemoryDependency = Annotated[
    MemoryFacade,
    Depends(get_memory),
]


async def require_owned_thread(
    *,
    memory: MemoryFacade,
    user: CurrentUser,
    thread_id: str,
) -> None:
    belongs = await memory.thread_belongs_to_user(
        user_id=user.user_id,
        thread_id=thread_id,
    )

    if not belongs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found or unavailable",
        )
