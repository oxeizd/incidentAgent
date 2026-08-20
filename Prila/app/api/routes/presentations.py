from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    CurrentUserDependency,
    MemoryDependency,
)
from app.memory.artifacts.presentations.document import (
    PresentationDocument,
)
from app.memory.artifacts.presentations.renderer import (
    render_presentation,
)
from app.memory.facade import PresentationOwnershipError


router = APIRouter(
    prefix="/api/v1/presentations",
    tags=["presentations"],
)


class PresentationFieldsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: PresentationDocument


@router.get("/mine")
async def list_my_presentations(
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, Any]:
    presentations = await memory.list_my_presentations(
        user.user_id,
    )

    return {
        "presentations": [
            item.model_dump(mode="json")
            for item in presentations
        ],
    }


@router.get("/shared")
async def list_shared_presentations(
    _: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, Any]:
    presentations = await memory.list_shared_presentations()

    return {
        "presentations": [
            item.model_dump(mode="json")
            for item in presentations
        ],
    }


@router.get("/{presentation_id}")
async def get_presentation(
    presentation_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, Any]:
    try:
        record = await memory.get_visible_presentation(
            user_id=user.user_id,
            presentation_id=presentation_id,
        )
    except PresentationOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or unavailable",
        ) from exc

    return record.model_dump(mode="json")


@router.get("/{presentation_id}/file")
async def download_presentation(
    presentation_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
    version: Literal["draft", "published"] = "published",
) -> HTMLResponse:
    try:
        record = await memory.get_visible_presentation(
            user_id=user.user_id,
            presentation_id=presentation_id,
        )
    except PresentationOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or unavailable",
        ) from exc

    if version == "published":
        if (
            record.status != "published"
            or record.published_snapshot is None
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Published presentation version was not found",
            )

        document = record.published_snapshot
    else:
        if record.owner_user_id != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Only the owner can download a draft presentation"
                ),
            )

        document = record.fields

    return HTMLResponse(
        content=render_presentation(document),
        headers={
            "Content-Disposition": (
                "attachment; "
                f'filename="{presentation_id}-{version}.html"'
            )
        },
    )


@router.patch("/{presentation_id}")
async def update_presentation(
    presentation_id: str,
    payload: PresentationFieldsUpdate,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, str]:
    updated = await memory.update_presentation_fields(
        user_id=user.user_id,
        presentation_id=presentation_id,
        fields=payload.fields,
    )

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or not owned by you",
        )

    return {
        "status": "ok",
    }


@router.post("/{presentation_id}/publish")
async def publish_presentation(
    presentation_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, str]:
    published = await memory.publish_presentation(
        user_id=user.user_id,
        presentation_id=presentation_id,
    )

    if not published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or not owned by you",
        )

    return {
        "status": "published",
    }


@router.post("/{presentation_id}/unpublish")
async def unpublish_presentation(
    presentation_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, str]:
    unpublished = await memory.unpublish_presentation(
        user_id=user.user_id,
        presentation_id=presentation_id,
    )

    if not unpublished:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or not owned by you",
        )

    return {
        "status": "draft",
    }


@router.delete("/{presentation_id}")
async def delete_presentation(
    presentation_id: str,
    user: CurrentUserDependency,
    memory: MemoryDependency,
) -> dict[str, str]:
    deleted = await memory.delete_presentation(
        user_id=user.user_id,
        presentation_id=presentation_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Presentation not found or not owned by you",
        )

    return {
        "status": "deleted",
    }
