from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.memory.facade import MemoryFacade


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """
    Trusted identity, установленная внешним authentication middleware.

    Memory API не валидирует JWT/SSO/cookies/API keys и не принимает user_id
    от клиента в JSON/query params. Внешний host обязан заполнить:
        request.state.current_user = AuthenticatedUser(...)
    """

    id: str
    permissions: frozenset[str] = frozenset()


async def get_memory(request: Request) -> MemoryFacade:
    memory = getattr(request.app.state, "memory", None)

    if not isinstance(memory, MemoryFacade):
        raise RuntimeError("Memory facade is not initialized")

    return memory


async def get_current_user(request: Request) -> AuthenticatedUser:
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
    current_user: Annotated[
        AuthenticatedUser,
        Depends(get_current_user),
    ],
) -> AuthenticatedUser:
    """
    Импорт и vector backfill — дорогие mutating операции, поэтому
    доступны только явному memory administrator.
    """
    if "memory:admin" not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memory administrative permission is required",
        )

    return current_user