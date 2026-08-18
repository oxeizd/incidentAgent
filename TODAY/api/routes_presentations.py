from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_memory, require_user_id
from app.memory.artifacts.presentations.document import PresentationDocument
from app.memory.artifacts.presentations.renderer import render_presentation
from app.memory.facade import MemoryFacade, PresentationOwnershipError

router = APIRouter(prefix="/api/v1/presentations", tags=["presentations"])


class PresentationFieldsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: PresentationDocument


@router.get("/mine")
async def list_mine(request: Request) -> dict[str, Any]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    return {"presentations": [record.model_dump(mode="json") for record in await memory.list_my_presentations(user_id)]}


@router.get("/shared")
async def list_shared(request: Request) -> dict[str, Any]:
    require_user_id(request)
    memory = get_memory(request)
    return {"presentations": [record.model_dump(mode="json") for record in await memory.list_shared_presentations()]}


@router.get("/{presentation_id}")
async def get_presentation(presentation_id: str, request: Request) -> dict[str, Any]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    try:
        return (await memory.get_visible_presentation(user_id=user_id, presentation_id=presentation_id)).model_dump(mode="json")
    except PresentationOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{presentation_id}/file")
async def download_presentation(presentation_id: str, request: Request, version: Literal["draft", "published"] = "published") -> HTMLResponse:
    user_id = require_user_id(request)
    memory = get_memory(request)
    try:
        record = await memory.get_visible_presentation(user_id=user_id, presentation_id=presentation_id)
    except PresentationOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if version == "published":
        if record.status != "published" or record.published_snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Published presentation version was not found")
        document = record.published_snapshot
    else:
        if record.owner_user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can download a draft presentation")
        document = record.fields
    return HTMLResponse(content=render_presentation(document), headers={"Content-Disposition": f'attachment; filename="{presentation_id}-{version}.html"'})


@router.patch("/{presentation_id}")
async def update_presentation(presentation_id: str, payload: PresentationFieldsUpdate, request: Request) -> dict[str, str]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    updated = await memory.update_presentation_fields(user_id=user_id, presentation_id=presentation_id, fields=payload.fields)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found or not owned by you")
    return {"status": "ok"}


@router.post("/{presentation_id}/publish")
async def publish_presentation(presentation_id: str, request: Request) -> dict[str, str]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    if not await memory.publish_presentation(user_id=user_id, presentation_id=presentation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found or not owned by you")
    return {"status": "published"}


@router.post("/{presentation_id}/unpublish")
async def unpublish_presentation(presentation_id: str, request: Request) -> dict[str, str]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    if not await memory.unpublish_presentation(user_id=user_id, presentation_id=presentation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found or not owned by you")
    return {"status": "draft"}


@router.delete("/{presentation_id}")
async def delete_presentation(presentation_id: str, request: Request) -> dict[str, str]:
    user_id = require_user_id(request)
    memory = get_memory(request)
    if not await memory.delete_presentation(user_id=user_id, presentation_id=presentation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found or not owned by you")
    return {"status": "deleted"}
