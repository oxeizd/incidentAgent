from __future__ import annotations

from typing import Any

from memory.artifacts.assignments.repository import AssignmentRepository
from memory.artifacts.incidents.repository import IncidentRepository
from memory.artifacts.search_results.repository import SearchResultRepository
from memory.search.contracts import EntityType


class SearchResultNotFoundError(Exception):
    """Search result does not exist, expired, or belongs to another user."""


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
    Return one UI-ready page of a saved search result.

    The snapshot defines the original result composition and ranking order.
    Domain repositories provide current row payloads. A deleted source row is
    returned as available=False without changing the saved row position.
    """
    result = await search_result_repository.get(
        result_id=result_id,
        owner_user_id=owner_user_id,
    )
    if result is None:
        raise SearchResultNotFoundError(
            f"Search result {result_id!r} not found, expired, or inaccessible"
        )

    page = await search_result_repository.get_page(
        result_id=result_id,
        owner_user_id=owner_user_id,
        cursor_value=cursor,
        limit=limit,
    )
    if page is None:
        raise SearchResultNotFoundError(
            f"Search result {result_id!r} not found, expired, or inaccessible"
        )

    entity_ids = [item.entity_id for item in page.items]

    hydrated = await hydrate_page_items(
        entity=result.entity,
        entity_ids=entity_ids,
        incident_repository=incident_repository,
        assignment_repository=assignment_repository,
    )

    items: list[dict[str, Any]] = []

    for item in page.items:
        payload = hydrated[item.entity_id]

        if payload is None:
            items.append(
                {
                    "entity_id": item.entity_id,
                    "position": item.position,
                    "score": item.score,
                    "available": False,
                    "deleted": True,
                    "payload": None,
                }
            )
            continue

        items.append(
            {
                "entity_id": item.entity_id,
                "position": item.position,
                "score": item.score,
                "available": True,
                "deleted": False,
                "payload": payload,
            }
        )

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
    """Load current domain rows for a saved-result page in one SQL query."""
    if entity == "incidents":
        found = await incident_repository.get_many(entity_ids)
    else:
        found = await assignment_repository.get_many(entity_ids)

    return {
        entity_id: found.get(entity_id)
        for entity_id in entity_ids
    }