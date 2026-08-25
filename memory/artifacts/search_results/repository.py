from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.db.connection import Database
from app.memory.search.contracts import (
    DisplaySchema,
    EntityType,
    ResultCursorPage,
    SearchResultItemRef,
    SearchResultRecord,
)
from app.memory.search.cursor import ResultCursor, decode_cursor, encode_cursor


SEARCH_RESULT_TTL = timedelta(days=7)
BUILDING_RESULT_TTL = timedelta(hours=1)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_APPEND_BATCH_SIZE = 1_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SearchResultRepository:
    """
    Immutable persisted snapshots search results.

    Writer создаёт result в status=building, append_items фиксирует
    точный порядок, затем mark_ready публикует snapshot. Domain payloads
    намеренно не дублируются: UI гидратирует их отдельными repositories.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_building(
        self,
        *,
        owner_user_id: str,
        source_thread_id: str | None,
        entity: EntityType,
        query: dict[str, Any],
        display: DisplaySchema,
        now: datetime | None = None,
    ) -> SearchResultRecord:
        normalized_owner_id = owner_user_id.strip()
        if not normalized_owner_id:
            raise ValueError("owner_user_id is required")

        created_at = now or utc_now()
        expires_at = created_at + SEARCH_RESULT_TTL
        result_id = f"search-{uuid.uuid4().hex}"

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO search_results (
                    id,
                    owner_user_id,
                    source_thread_id,
                    entity,
                    query_json,
                    display_json,
                    total_count,
                    status,
                    artifact_version,
                    created_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 'building', 1, ?, ?)
                """,
                (
                    result_id,
                    normalized_owner_id,
                    source_thread_id,
                    entity,
                    _json_dump(query),
                    display.model_dump_json(),
                    utc_iso(created_at),
                    utc_iso(expires_at),
                ),
            )

        return SearchResultRecord(
            id=result_id,
            owner_user_id=normalized_owner_id,
            source_thread_id=source_thread_id,
            entity=entity,
            query=query,
            display=display,
            total_count=0,
            status="building",
            artifact_version=1,
            created_at=created_at,
            expires_at=expires_at,
        )

    async def append_items(
        self,
        *,
        result_id: str,
        items: Sequence[SearchResultItemRef],
    ) -> None:
        if not items:
            return

        if len(items) > MAX_APPEND_BATCH_SIZE:
            raise ValueError(
                "Too many items in one search-result append batch: "
                f"{len(items)} > {MAX_APPEND_BATCH_SIZE}"
            )

        _validate_consecutive_positions(items)

        async with self._database.transaction() as connection:
            select_cursor = await connection.execute(
                """
                SELECT status, total_count
                FROM search_results
                WHERE id = ?
                """,
                (result_id,),
            )
            row = await select_cursor.fetchone()

            if row is None:
                raise ValueError(
                    f"Search result does not exist: {result_id}"
                )

            if row["status"] != "building":
                raise ValueError(
                    "Cannot append items to search result in status "
                    f"{row['status']!r}"
                )

            expected_start = int(row["total_count"])
            if items[0].position != expected_start:
                raise ValueError(
                    "Batch position does not continue stored snapshot: "
                    f"expected {expected_start}, "
                    f"got {items[0].position}"
                )

            await connection.executemany(
                """
                INSERT INTO search_result_items (
                    search_result_id,
                    position,
                    entity_id,
                    score
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        result_id,
                        item.position,
                        item.entity_id,
                        item.score,
                    )
                    for item in items
                ],
            )

            await connection.execute(
                """
                UPDATE search_results
                SET total_count = ?
                WHERE id = ?
                """,
                (
                    expected_start + len(items),
                    result_id,
                ),
            )

    async def mark_ready(
        self,
        *,
        result_id: str,
    ) -> None:
        async with self._database.transaction() as connection:
            update_cursor = await connection.execute(
                """
                UPDATE search_results
                SET status = 'ready'
                WHERE id = ?
                  AND status = 'building'
                """,
                (result_id,),
            )

        if update_cursor.rowcount != 1:
            raise ValueError(
                f"Cannot mark search result ready: {result_id}"
            )

    async def mark_failed(
        self,
        *,
        result_id: str,
    ) -> bool:
        async with self._database.transaction() as connection:
            update_cursor = await connection.execute(
                """
                UPDATE search_results
                SET status = 'failed'
                WHERE id = ?
                  AND status = 'building'
                """,
                (result_id,),
            )

        return update_cursor.rowcount == 1

    async def get(
        self,
        *,
        result_id: str,
        owner_user_id: str,
        now: datetime | None = None,
    ) -> SearchResultRecord | None:
        connection = await self._database.read_connection()

        select_cursor = await connection.execute(
            """
            SELECT *
            FROM search_results
            WHERE id = ?
              AND owner_user_id = ?
              AND status = 'ready'
              AND invalidated_at IS NULL
            """,
            (result_id, owner_user_id),
        )
        row = await select_cursor.fetchone()

        if row is None:
            return None

        record = self._row_to_record(row)

        if record.expires_at <= (now or utc_now()):
            return None

        return record

    async def get_page(
        self,
        *,
        result_id: str,
        owner_user_id: str,
        cursor_value: str | None,
        limit: int | None,
        now: datetime | None = None,
    ) -> ResultCursorPage | None:
        result = await self.get(
            result_id=result_id,
            owner_user_id=owner_user_id,
            now=now,
        )
        if result is None:
            return None

        page_size = _normalize_page_size(limit)
        after_position = -1

        if cursor_value:
            decoded_cursor = decode_cursor(cursor_value)

            if decoded_cursor.result_id != result_id:
                raise ValueError(
                    "Cursor does not belong to this search result"
                )

            after_position = decoded_cursor.after_position

        connection = await self._database.read_connection()

        page_cursor = await connection.execute(
            """
            SELECT
                position,
                entity_id,
                score
            FROM search_result_items
            WHERE search_result_id = ?
              AND position > ?
            ORDER BY position ASC
            LIMIT ?
            """,
            (
                result_id,
                after_position,
                page_size + 1,
            ),
        )
        rows = await page_cursor.fetchall()

        visible_rows = rows[:page_size]
        has_more = len(rows) > page_size

        items = [
            SearchResultItemRef(
                position=int(row["position"]),
                entity_id=str(row["entity_id"]),
                score=(
                    float(row["score"])
                    if row["score"] is not None
                    else None
                ),
            )
            for row in visible_rows
        ]

        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor(
                ResultCursor(
                    result_id=result_id,
                    after_position=items[-1].position,
                )
            )

        return ResultCursorPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def invalidate(
        self,
        *,
        result_id: str,
        owner_user_id: str,
        now: datetime | None = None,
    ) -> bool:
        async with self._database.transaction() as connection:
            update_cursor = await connection.execute(
                """
                UPDATE search_results
                SET invalidated_at = ?
                WHERE id = ?
                  AND owner_user_id = ?
                  AND invalidated_at IS NULL
                """,
                (
                    utc_iso(now or utc_now()),
                    result_id,
                    owner_user_id,
                ),
            )

        return update_cursor.rowcount > 0

    async def cleanup_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        current_time = now or utc_now()
        stale_building_before = (
            current_time - BUILDING_RESULT_TTL
        )

        async with self._database.transaction() as connection:
            delete_cursor = await connection.execute(
                """
                DELETE FROM search_results
                WHERE expires_at <= ?
                   OR invalidated_at IS NOT NULL
                   OR status = 'failed'
                   OR (
                       status = 'building'
                       AND created_at <= ?
                   )
                """,
                (
                    utc_iso(current_time),
                    utc_iso(stale_building_before),
                ),
            )

        return delete_cursor.rowcount

    @staticmethod
    def _row_to_record(row: Any) -> SearchResultRecord:
        created_at = parse_utc(row["created_at"])
        expires_at = parse_utc(row["expires_at"])

        if created_at is None or expires_at is None:
            raise RuntimeError(
                "Stored search result has invalid timestamps"
            )

        return SearchResultRecord(
            id=str(row["id"]),
            owner_user_id=str(row["owner_user_id"]),
            source_thread_id=(
                str(row["source_thread_id"])
                if row["source_thread_id"] is not None
                else None
            ),
            entity=row["entity"],
            query=_json_load(row["query_json"]),
            display=DisplaySchema.model_validate_json(
                row["display_json"]
            ),
            total_count=int(row["total_count"]),
            status=row["status"],
            artifact_version=int(row["artifact_version"]),
            created_at=created_at,
            expires_at=expires_at,
            invalidated_at=parse_utc(row["invalidated_at"]),
        )


def _validate_consecutive_positions(
    items: Sequence[SearchResultItemRef],
) -> None:
    if not items:
        return

    start = items[0].position

    for offset, item in enumerate(items):
        if item.position != start + offset:
            raise ValueError(
                "Search result item positions must be consecutive"
            )


def _normalize_page_size(value: int | None) -> int:
    if value is None:
        return DEFAULT_PAGE_SIZE

    return max(1, min(value, MAX_PAGE_SIZE))


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _json_load(value: str) -> dict[str, Any]:
    decoded = json.loads(value)

    if not isinstance(decoded, dict):
        raise ValueError("Stored query_json must be an object")

    return decoded