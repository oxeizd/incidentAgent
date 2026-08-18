from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import require_owned_thread, require_user_id
from app.api.run_service import (
    ConversationRunService,
    InvalidToolOutputError,
    RunConflictError,
)
from app.api.schemas import CreateRunRequest


router = APIRouter(
    prefix="/api/v1/threads",
    tags=["runs"],
)


@router.post("/{thread_id}/runs")
async def create_run(
    thread_id: str,
    payload: CreateRunRequest,
    request: Request,
) -> EventSourceResponse:
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

    try:
        service.validate_request(
            thread_id=thread_id,
            request=payload,
        )
    except RunConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except (InvalidToolOutputError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    async def event_source():
        try:
            async for event in service.create_run(
                thread_id=thread_id,
                user_id=user_id,
                request=payload,
            ):
                yield {
                    "event": event.type.value,
                    "id": event.event_id,
                    "data": json.dumps(
                        event.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                }
        except RunConflictError as exc:
            yield {
                "event": "run.failed",
                "data": json.dumps(
                    {
                        "detail": str(exc),
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_source())