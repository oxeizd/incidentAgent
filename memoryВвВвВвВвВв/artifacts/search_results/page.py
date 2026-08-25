from __future__ import annotations

from typing import Any

from app.memory.artifacts.assignments.repository import AssignmentRepository
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.artifacts.search_results.repository import (
    SearchResultRepository,
)
from app.memory.search.contracts import EntityType


class SearchResultNotFoundError(Exception):
    """Search result is missing, expired, invalidated, or inaccessible."""


async def get_search_result_table_page(
    *,
    search_result_repository: SearchResultRepository,
    incident_repository: IncidentRepository,
    assignment_repository: AssignmentRepository,
    result_id: str,
    owner_user_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Returns one UI-ready page from a persisted search-result snapshot.

    The snapshot owns the result composition and ordering. Current domain
    repositories hydrate payloads. A source row deleted after snapshot
    creation is preserved in its saved position as unavailable.
    """
    result_and_page = await search_result_repository.get_page(
        result_id=result_id,
        owner_user_id=owner_user_id,
        cursor_value=cursor,
        limit=limit,
    )

    if result_and_page is None:
        raise SearchResultNotFoundError(
            f"Search result {result_id!r} not found, expired, "
            "or inaccessible"
        )

    result, page = result_and_page
    entity_ids = [item.entity_id for item in page.items]

    hydrated = await hydrate_page_items(
        entity=result.entity,
        entity_ids=entity_ids,
        incident_repository=incident_repository,
        assignment_repository=assignment_repository,
    )

    items = [
        _page_item_payload(
            entity_id=item.entity_id,
            position=item.position,
            score=item.score,
            payload=hydrated[item.entity_id],
        )
        for item in page.items
    ]

    return {
        "artifact_type": "memory.search_result_table_page",
        "artifact_version": 1,
        "result_id": result.id,
        "entity": result.entity,
        "total_count": result.total_count,
        "display": result.display.model_dump(mode="json"),
        "items": items,
        "page": {
            "returned_count": len(items),
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        },
    }


async def hydrate_page_items(
    *,
    entity: EntityType,
    entity_ids: list[str],
    incident_repository: IncidentRepository,
    assignment_repository: AssignmentRepository,
) -> dict[str, dict[str, Any] | None]:
    """Loads current domain rows for a saved page in one repository call."""
    if entity == "incidents":
        found = await incident_repository.get_many(entity_ids)
    elif entity == "assignments":
        found = await assignment_repository.get_many(entity_ids)
    else:
        raise ValueError(f"Unsupported search result entity: {entity!r}")

    return {
        entity_id: found.get(entity_id)
        for entity_id in entity_ids
    }


def _page_item_payload(
    *,
    entity_id: str,
    position: int,
    score: float | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if payload is None:
        return {
            "entity_id": entity_id,
            "position": position,
            "score": score,
            "available": False,
            "deleted": True,
            "payload": None,
        }

    return {
        "entity_id": entity_id,
        "position": position,
        "score": score,
        "available": True,
        "deleted": False,
        "payload": payload,
    }