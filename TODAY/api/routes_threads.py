from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.dependencies import (
    get_memory,
    require_owned_thread,
    require_user_id,
)
from app.api.schemas import (
    CreateThreadRequest,
    ThreadMessage,
    ThreadResponse,
)


router = APIRouter(
    prefix="/api/v1/threads",
    tags=["threads"],
)


@router.post(
    "",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    payload: CreateThreadRequest,
    request: Request,
) -> ThreadResponse:
    user_id = require_user_id(request)
    memory = get_memory(request)

    thread = await memory.create_thread_for_user(
        user_id=user_id,
        title=payload.title,
    )

    return ThreadResponse(
        id=str(thread["id"]),
        title=thread.get("title"),
        created_at=str(thread["created_at"]),
        updated_at=str(thread["updated_at"]),
    )


@router.get(
    "",
    response_model=list[ThreadResponse],
)
async def list_threads(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=200),
) -> list[ThreadResponse]:
    """
    Временно сохраняет прежний list contract главного API.

    Для cursor-pagination UI может использовать самостоятельный memory API.
    Позже можно сменить response на ThreadPage без изменений MemoryFacade.
    """
    user_id = require_user_id(request)
    memory = get_memory(request)

    result = await memory.list_threads_for_user(
        user_id=user_id,
        limit=limit,
    )

    return [
        ThreadResponse(
            id=str(item["id"]),
            title=item.get("title"),
            created_at=str(item["created_at"]),
            updated_at=str(item["updated_at"]),
        )
        for item in result["items"]
    ]


@router.get(
    "/{thread_id}/messages",
    response_model=list[ThreadMessage],
)
async def list_messages(
    thread_id: str,
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=200),
    before: str | None = Query(default=None),
) -> list[ThreadMessage]:
    user_id = require_user_id(request)

    memory = await require_owned_thread(
        request=request,
        thread_id=thread_id,
        user_id=user_id,
    )

    page = await memory.get_thread_messages_for_user(
        user_id=user_id,
        thread_id=thread_id,
        limit=limit,
        before=before,
    )

    return [
        ThreadMessage.model_validate(item)
        for item in page["items"]
    ]


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str,
    request: Request,
) -> dict[str, str]:
    user_id = require_user_id(request)
    memory = get_memory(request)

    deleted = await memory.delete_thread_for_user(
        user_id=user_id,
        thread_id=thread_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )

    return {
        "status": "deleted",
    }


@router.get("/{thread_id}/state")
async def thread_state(
    thread_id: str,
    request: Request,
) -> dict[str, Any]:
    user_id = require_user_id(request)

    await require_owned_thread(
        request=request,
        thread_id=thread_id,
        user_id=user_id,
    )

    service: ConversationRunService | None = getattr(
        request.app.state,
        "run_service",
        None,
    )

    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Conversation graph is not initialized",
        )

    return await service.get_state(
        thread_id=thread_id,
    )