from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from memory.api.contracts import (
    BackfillResponse,
    CreateThreadRequest,
    ImportResponse,
    MessageResponse,
    SemanticSearchRequest,
    StructuredSearchRequest,
)
from memory.api.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_memory,
    require_memory_admin,
)
from memory.facade import MemoryAccessError, MemoryFacade
from memory.search.service import ThreadOwnershipError


router = APIRouter(prefix="/api/v1/memory", tags=["memory"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
Memory = Annotated[MemoryFacade, Depends(get_memory)]
MemoryAdmin = Annotated[AuthenticatedUser, Depends(require_memory_admin)]


@router.get("/health")
async def health(
    memory: Memory,
) -> dict[str, object]:
    return await memory.healthcheck()


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    request: CreateThreadRequest,
    current_user: CurrentUser,
    memory: Memory,
) -> dict[str, str]:
    thread_id = await memory.application.threads.create_thread(
        user_id=current_user.id,
        title=request.title,
    )
    return {"thread_id": thread_id}


@router.post(
    "/search/incidents",
    response_model=MessageResponse,
)
async def search_incidents(
    request: StructuredSearchRequest,
    current_user: CurrentUser,
    memory: Memory,
) -> MessageResponse:
    try:
        message_id = await memory.search_incidents(
            user_id=current_user.id,
            thread_id=request.thread_id,
            filters=request.filters,
            preview_limit=request.preview_limit,
        )
    except ThreadOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thread does not belong to the current user",
        ) from exc

    return MessageResponse(message_id=message_id)


@router.post(
    "/search/assignments",
    response_model=MessageResponse,
)
async def search_assignments(
    request: StructuredSearchRequest,
    current_user: CurrentUser,
    memory: Memory,
) -> MessageResponse:
    try:
        message_id = await memory.search_assignments(
            user_id=current_user.id,
            thread_id=request.thread_id,
            filters=request.filters,
            preview_limit=request.preview_limit,
        )
    except ThreadOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thread does not belong to the current user",
        ) from exc

    return MessageResponse(message_id=message_id)


@router.post(
    "/search/similar/incidents",
    response_model=MessageResponse,
)
async def similar_incidents(
    request: SemanticSearchRequest,
    current_user: CurrentUser,
    memory: Memory,
) -> MessageResponse:
    try:
        message_id = await memory.find_similar_incidents(
            user_id=current_user.id,
            thread_id=request.thread_id,
            query_text=request.query_text,
            limit=request.limit,
            preview_limit=request.preview_limit,
        )
    except ThreadOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thread does not belong to the current user",
        ) from exc

    return MessageResponse(message_id=message_id)


@router.post(
    "/search/similar/assignments",
    response_model=MessageResponse,
)
async def similar_assignments(
    request: SemanticSearchRequest,
    current_user: CurrentUser,
    memory: Memory,
) -> MessageResponse:
    try:
        message_id = await memory.find_similar_assignments(
            user_id=current_user.id,
            thread_id=request.thread_id,
            query_text=request.query_text,
            limit=request.limit,
            preview_limit=request.preview_limit,
        )
    except ThreadOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thread does not belong to the current user",
        ) from exc

    return MessageResponse(message_id=message_id)


@router.get("/search-results/{result_id}")
async def open_search_result(
    result_id: str,
    current_user: CurrentUser,
    memory: Memory,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    try:
        return await memory.open_search_result(
            user_id=current_user.id,
            result_id=result_id,
            cursor=cursor,
            limit=limit,
        )
    except MemoryAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search result was not found or has expired",
        ) from exc


@router.get("/incidents/{number}")
async def get_incident(
    number: str,
    current_user: CurrentUser,
    memory: Memory,
) -> dict[str, Any]:
    incident = await memory.get_incident(number=number)

    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident was not found",
        )

    return incident


@router.get("/assignments/{assignment_id}")
async def get_assignment(
    assignment_id: str,
    current_user: CurrentUser,
    memory: Memory,
) -> dict[str, Any]:
    assignment = await memory.get_assignment(
        assignment_id=assignment_id,
    )

    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment was not found",
        )

    return assignment


@router.post(
    "/imports/{entity}",
    response_model=ImportResponse,
)
async def import_json(
    entity: str,
    file: Annotated[UploadFile, File(...)],
    _: MemoryAdmin,
    memory: Memory,
    max_errors: int = Query(default=100, ge=1, le=1_000),
) -> ImportResponse:
    if entity not in {"incidents", "assignments"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity must be incidents or assignments",
        )

    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .json import files are accepted",
        )

    try:
        content = await file.read()
        raw = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Import file must be UTF-8 JSON",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON: {exc.msg}",
        ) from exc
    finally:
        await file.close()

    report = await memory.application.imports.import_data(
        entity=entity,
        raw=raw,
        max_errors=max_errors,
    )

    return ImportResponse(
        entity=report.entity,
        total_items=report.total_items,
        imported_count=report.imported_count,
        failed_count=report.failed_count,
        errors=[
            error.model_dump(mode="json")
            for error in report.errors
        ],
    )


@router.post(
    "/admin/backfill-vectors",
    response_model=BackfillResponse,
)
async def backfill_vectors(
    _: MemoryAdmin,
    memory: Memory,
    entity: str = Query(
        default="all",
        pattern="^(incidents|assignments|all)$",
    ),
    batch_size: int = Query(default=100, ge=1, le=1_000),
) -> BackfillResponse:
    response = BackfillResponse()

    if entity in {"incidents", "all"}:
        response.incidents_processed = (
            await memory.application.vector_backfill.backfill_incidents(
                batch_size=batch_size,
            )
        )

    if entity in {"assignments", "all"}:
        response.assignments_processed = (
            await memory.application.vector_backfill.backfill_assignments(
                batch_size=batch_size,
            )
        )

    return response