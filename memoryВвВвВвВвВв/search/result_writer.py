from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from app.memory.artifacts.search_results.repository import (
    SearchResultRepository,
)
from app.memory.artifacts.threads.repository import ThreadRepository
from app.memory.errors import ThreadOwnershipError
from app.memory.search.contracts import (
    DisplaySchema,
    EntityType,
    PreviewRow,
    SearchExecution,
    SearchHit,
    SearchPreview,
    SearchResultItemRef,
    SearchResultReferenceArtifact,
)
from app.memory.search.entity_profiles import (
    get_entity_display_profiles,
)
from app.memory.search.projection import project_row


logger = logging.getLogger(__name__)

MAX_PREVIEW_LIMIT = 20


class SearchResultWriter:
    """
    Persists immutable search-result snapshots.

    Query execution and ranking happen outside this class. It owns only:
    thread ownership verification, snapshot persistence and bounded preview
    creation for chat/UI.
    """

    def __init__(
        self,
        *,
        search_result_repository: SearchResultRepository,
        thread_repository: ThreadRepository,
        default_preview_limit: int,
    ) -> None:
        if default_preview_limit < 1:
            raise ValueError(
                "default_preview_limit must be at least 1"
            )

        self._search_result_repository = search_result_repository
        self._thread_repository = thread_repository
        self._default_preview_limit = min(
            default_preview_limit,
            MAX_PREVIEW_LIMIT,
        )

    async def write_execution(
        self,
        *,
        execution: SearchExecution,
        owner_user_id: str,
        thread_id: str,
        preview_limit: int | None = None,
        now: datetime | None = None,
    ) -> SearchResultReferenceArtifact:
        """
        Persists an already materialized SearchExecution.

        This is the normal path after unified search. write_stream remains
        available only for a future truly streaming query source.
        """
        await self._require_thread_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )

        preview_size = self._resolve_preview_limit(preview_limit)
        profiles = get_entity_display_profiles(execution.entity)
        preview_schema = profiles.preview
        table_schema = profiles.table

        result = await self._search_result_repository.create_building(
            owner_user_id=owner_user_id,
            source_thread_id=thread_id,
            entity=execution.entity,
            query=execution.normalized_query,
            display=table_schema,
            now=now,
        )

        try:
            await self._append_execution_items(
                result_id=result.id,
                hits=execution.hits,
            )
            await self._search_result_repository.mark_ready(
                result_id=result.id,
            )
        except Exception:
            logger.exception(
                "Search result persistence failed: "
                "result_id=%s entity=%s",
                result.id,
                execution.entity,
            )
            await self._mark_failed_safely(result.id)
            raise

        preview_rows = self._build_preview_rows(
            hits=execution.hits,
            preview_size=preview_size,
            preview_schema=preview_schema,
        )

        return SearchResultReferenceArtifact(
            result_id=result.id,
            entity=execution.entity,
            total_count=len(execution.hits),
            preview_count=len(preview_rows),
            display=preview_schema,
            preview=SearchPreview(rows=preview_rows),
            created_at=result.created_at,
            expires_at=result.expires_at,
        )

    async def write_stream(
        self,
        *,
        entity: EntityType,
        query: dict[str, object],
        owner_user_id: str,
        thread_id: str,
        stream: AsyncIterator[list[SearchHit]],
        preview_limit: int | None = None,
        now: datetime | None = None,
    ) -> SearchResultReferenceArtifact:
        """
        Persists a real async stream of hit batches.

        Kept because it is useful for future DB streaming, but unified search
        currently uses write_execution() directly.
        """
        await self._require_thread_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )

        preview_size = self._resolve_preview_limit(preview_limit)
        profiles = get_entity_display_profiles(entity)
        preview_schema = profiles.preview
        table_schema = profiles.table

        result = await self._search_result_repository.create_building(
            owner_user_id=owner_user_id,
            source_thread_id=thread_id,
            entity=entity,
            query=query,
            display=table_schema,
            now=now,
        )

        preview_rows: list[PreviewRow] = []
        position = 0

        try:
            async for hit_batch in stream:
                if not hit_batch:
                    continue

                await self._search_result_repository.append_items(
                    result_id=result.id,
                    items=[
                        SearchResultItemRef(
                            position=position + index,
                            entity_id=hit.entity_id,
                            score=hit.score,
                        )
                        for index, hit in enumerate(hit_batch)
                    ],
                )

                self._append_preview_rows(
                    preview_rows=preview_rows,
                    hits=hit_batch,
                    preview_size=preview_size,
                    preview_schema=preview_schema,
                )
                position += len(hit_batch)

            await self._search_result_repository.mark_ready(
                result_id=result.id,
            )
        except Exception:
            logger.exception(
                "Search result persistence failed: "
                "result_id=%s entity=%s",
                result.id,
                entity,
            )
            await self._mark_failed_safely(result.id)
            raise

        return SearchResultReferenceArtifact(
            result_id=result.id,
            entity=entity,
            total_count=position,
            preview_count=len(preview_rows),
            display=preview_schema,
            preview=SearchPreview(rows=preview_rows),
            created_at=result.created_at,
            expires_at=result.expires_at,
        )

    async def _append_execution_items(
        self,
        *,
        result_id: str,
        hits: Sequence[SearchHit],
    ) -> None:
        for start in range(
            0,
            len(hits),
            1_000,
        ):
            batch = hits[start : start + 1_000]

            await self._search_result_repository.append_items(
                result_id=result_id,
                items=[
                    SearchResultItemRef(
                        position=start + index,
                        entity_id=hit.entity_id,
                        score=hit.score,
                    )
                    for index, hit in enumerate(batch)
                ],
            )

    async def _require_thread_owner(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
    ) -> None:
        belongs_to_user = (
            await self._thread_repository.thread_belongs_to_user(
                thread_id=thread_id,
                user_id=owner_user_id,
            )
        )

        if not belongs_to_user:
            raise ThreadOwnershipError(
                f"Thread {thread_id!r} does not belong to current user"
            )

    async def _mark_failed_safely(
        self,
        result_id: str,
    ) -> None:
        try:
            await self._search_result_repository.mark_failed(
                result_id=result_id,
            )
        except Exception:
            logger.exception(
                "Unable to mark search result as failed: result_id=%s",
                result_id,
            )

    def _build_preview_rows(
        self,
        *,
        hits: Sequence[SearchHit],
        preview_size: int,
        preview_schema: DisplaySchema,
    ) -> list[PreviewRow]:
        return [
            project_row(
                entity_id=hit.entity_id,
                payload=hit.payload,
                schema=preview_schema,
            )
            for hit in hits[:preview_size]
        ]

    def _append_preview_rows(
        self,
        *,
        preview_rows: list[PreviewRow],
        hits: Sequence[SearchHit],
        preview_size: int,
        preview_schema: DisplaySchema,
    ) -> None:
        remaining = preview_size - len(preview_rows)

        if remaining <= 0:
            return

        preview_rows.extend(
            self._build_preview_rows(
                hits=hits[:remaining],
                preview_size=remaining,
                preview_schema=preview_schema,
            )
        )

    def _resolve_preview_limit(
        self,
        requested: int | None,
    ) -> int:
        if requested is None:
            return self._default_preview_limit

        if requested < 1:
            raise ValueError(
                "preview_limit must be at least 1"
            )

        return min(requested, MAX_PREVIEW_LIMIT)