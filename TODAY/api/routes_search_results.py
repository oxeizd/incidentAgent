from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.dependencies import get_memory, require_user_id
from app.memory.facade import MemoryAccessError

router = APIRouter(prefix="/api/v1/search-results", tags=["search-results"])


@router.get("/{result_id}")
async def open_search_result(
    result_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    try:
        return await memory.open_search_result(user_id=user_id, result_id=result_id, cursor=cursor, limit=limit)
    except MemoryAccessError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search result not found or expired") from exc
