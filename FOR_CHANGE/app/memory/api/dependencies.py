from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from memory.facade import MemoryFacade


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """
    Trusted identity supplied by the host application's auth layer.

    Memory does not verify passwords, JWTs, cookies, API keys, or SSO.
    The host application authenticates the request and puts this object into
    request.state.current_user before FastAPI calls memory endpoints.
    """

    id: str
    permissions: frozenset[str] = frozenset()


async def get_memory(request: Request) -> MemoryFacade:
    memory = getattr(request.app.state, "memory", None)

    if not isinstance(memory, MemoryFacade):
        raise RuntimeError("Memory facade is not initialized")

    return memory


async def get_current_user(request: Request) -> AuthenticatedUser:
    """
    Return the user established by upstream authentication middleware.

    Never take user_id from JSON or query parameters. That would allow a
    caller to impersonate any other account.
    """
    user = getattr(request.state, "current_user", None)

    if not isinstance(user, AuthenticatedUser):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
        )

    if not user.id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user has an empty ID",
        )

    return user


async def require_memory_admin(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """
    Restrict expensive and mutating operations: imports and vector backfill.
    """
    if "memory:admin" not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memory administrative permission is required",
        )

    return current_user