from __future__ import annotations

from datetime import datetime
from typing import Any

from memory.artifacts.search_results.repository import SearchResultRepository
from memory.search.contracts import (
    SearchExecution,
    SearchPreview,
    SearchResultItemRef,
    SearchResultReferenceArtifact,
)
from memory.search.profiles import get_display_profile
from memory.search.projection import project_row


DEFAULT_PREVIEW_LIMIT = 10
MAX_PREVIEW_LIMIT = 20


async def build_search_result_artifact(
    *,
    repository: SearchResultRepository,
    owner_user_id: str,
    source_thread_id: str | None,
    execution: SearchExecution,
    preview_profile_name: str,
    table_profile_name: str,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
    now: datetime | None = None,
) -> SearchResultReferenceArtifact:
    preview_limit = _normalize_preview_limit(preview_limit)

    preview_schema = get_display_profile(preview_profile_name)
    table_schema = get_display_profile(table_profile_name)

    _validate_profile_entity(
        entity=execution.entity,
        preview_profile=preview_schema.profile,
        table_profile=table_schema.profile,
    )

    item_refs = [
        SearchResultItemRef(
            position=index,
            entity_id=hit.entity_id,
            score=hit.score,
        )
        for index, hit in enumerate(execution.hits)
    ]

    record = await repository.create(
        owner_user_id=owner_user_id,
        source_thread_id=source_thread_id,
        entity=execution.entity,
        query=execution.normalized_query,
        display=table_schema,
        items=item_refs,
        now=now,
    )

    preview_rows = [
        project_row(
            entity_id=hit.entity_id,
            payload=hit.payload,
            schema=preview_schema,
        )
        for hit in execution.hits[:preview_limit]
    ]

    return SearchResultReferenceArtifact(
        result_id=record.id,
        entity=execution.entity,
        total_count=record.total_count,
        preview_count=len(preview_rows),
        display=preview_schema,
        preview=SearchPreview(rows=preview_rows),
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _normalize_preview_limit(value: int) -> int:
    return max(1, min(value, MAX_PREVIEW_LIMIT))


def _validate_profile_entity(
    *,
    entity: str,
    preview_profile: str,
    table_profile: str,
) -> None:
    expected_prefix = f"{entity}."

    if not preview_profile.startswith(expected_prefix):
        raise ValueError(
            f"Preview profile {preview_profile!r} does not match entity {entity!r}"
        )

    if not table_profile.startswith(expected_prefix):
        raise ValueError(
            f"Table profile {table_profile!r} does not match entity {entity!r}"
        )