from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    status,
)
from sse_starlette.sse import EventSourceResponse

from app.api.conversation_service import (
    ConversationRunConflictError,
    ConversationService,
)
from app.api.dependencies import (
    CurrentUserDependency,
    MemoryDependency,
    require_owned_thread,
)
from app.api.events import (
    as_sse_data,
    done_sse_event,
    error_event,
    new_stream_id,
    unix_now,
)
from app.api.schemas import (
    ConversationTurnRequest,
    CreateThreadRequest,
    ThreadMessage,
    ThreadResponse,
)


router = APIRouter(
    prefix="/api/v1/threads",
    tags=["threads"],
)


def _get_conversation_service(
    request: Request,
) -> ConversationService:
    service = getattr(
        request.app.state,
        "conversation_service",
        None,
    )

    if not isinstance(service, ConversationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service is not initialized",
        )

    return service


@router.post(
    "",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    payload: CreateThreadRequest,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> ThreadResponse:
    thread = await memory.create_thread_for_user(
        user_id=user.user_id,
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
    user: CurrentUserDependency,
    memory: MemoryDependency,
    limit: int | None = Query(
        default=None,
        ge=1,
        le=200,
    ),
) -> list[ThreadResponse]:
    result = await memory.list_threads_for_user(
        user_id=user.user_id,
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


@router.post("/{thread_id}/turns")
async def create_turn(
    thread_id: str,
    payload: ConversationTurnRequest,
    request: Request,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> EventSourceResponse:
    """
    Один пользовательский ход единого conversation graph.

    Независимо от того, отвечает пользователь на Interaction, уточняет
    задачу, отменяет её или запускает новую, transport передаёт один
    нормализованный text. Guard и workflow определяют смысл реплики.
    """
    await require_owned_thread(
        memory=memory,
        user=user,
        thread_id=thread_id,
    )

    service = _get_conversation_service(request)

    try:
        service.validate_turn(thread_id=thread_id)
    except ConversationRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    async def event_source():
        stream_id = new_stream_id()
        created = unix_now()

        try:
            async for payload_event in service.run_turn(
                thread_id=thread_id,
                user_id=user.user_id,
                request=payload,
            ):
                yield as_sse_data(payload_event)

        except ConversationRunConflictError as exc:
            yield as_sse_data(
                error_event(
                    stream_id=stream_id,
                    created=created,
                    message=str(exc),
                    retryable=True,
                )
            )
        except Exception:
            yield as_sse_data(
                error_event(
                    stream_id=stream_id,
                    created=created,
                    message=(
                        "Не удалось обработать запрос. "
                        "Попробуйте ещё раз."
                    ),
                    retryable=True,
                )
            )
        finally:
            yield done_sse_event()

    return EventSourceResponse(event_source())


@router.get(
    "/{thread_id}/messages",
    response_model=list[ThreadMessage],
)
async def list_messages(
    thread_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
    limit: int | None = Query(
        default=None,
        ge=1,
        le=200,
    ),
    before: str | None = Query(default=None),
) -> list[ThreadMessage]:
    await require_owned_thread(
        memory=memory,
        user=user,
        thread_id=thread_id,
    )

    page = await memory.get_thread_messages_for_user(
        user_id=user.user_id,
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
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, str]:
    deleted = await memory.delete_thread_for_user(
        user_id=user.user_id,
        thread_id=thread_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found or unavailable",
        )

    return {
        "status": "deleted",
    }