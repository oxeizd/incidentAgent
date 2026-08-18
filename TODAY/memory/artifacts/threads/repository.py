from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any


MESSAGE_ROLES = frozenset({"user", "assistant", "system", "tool"})

DEFAULT_MESSAGES_PAGE_SIZE = 50
MAX_MESSAGES_PAGE_SIZE = 200

DEFAULT_THREADS_PAGE_SIZE = 50
MAX_THREADS_PAGE_SIZE = 200


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ThreadRepository:
    """
    Persistence для пользовательских тредов и immutable истории сообщений.

    - threads принадлежат одному user_id;
    - messages append-only;
    - threads.updated_at меняется при добавлении сообщения;
    - pagination идёт от новых сообщений к старым.
    """

    def __init__(self, database) -> None:
        self._database = database

    async def create_thread(
        self,
        *,
        user_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = _required_text(user_id, "user_id")
        normalized_title = _optional_text(title)

        thread_id = f"thread-{uuid.uuid4().hex}"
        now = utc_now_iso()

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO threads (
                    id,
                    user_id,
                    title,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    normalized_user_id,
                    normalized_title,
                    now,
                    now,
                ),
            )

        return {
            "id": thread_id,
            "user_id": normalized_user_id,
            "title": normalized_title,
            "created_at": now,
            "updated_at": now,
        }

    async def get_thread(
        self,
        *,
        thread_id: str,
    ) -> dict[str, Any] | None:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT
                id,
                user_id,
                title,
                created_at,
                COALESCE(updated_at, created_at) AS updated_at
            FROM threads
            WHERE id = ?
            """,
            (thread_id,),
        )
        row = await cursor.fetchone()

        return dict(row) if row is not None else None

    async def get_thread_owner(
        self,
        *,
        thread_id: str,
    ) -> str | None:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT user_id
            FROM threads
            WHERE id = ?
            """,
            (thread_id,),
        )
        row = await cursor.fetchone()

        return str(row["user_id"]) if row is not None else None

    async def thread_belongs_to_user(
        self,
        *,
        thread_id: str,
        user_id: str,
    ) -> bool:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT 1
            FROM threads
            WHERE id = ? AND user_id = ?
            """,
            (thread_id, user_id),
        )

        return await cursor.fetchone() is not None

    async def list_threads(
        self,
        *,
        user_id: str,
        cursor_value: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает треды пользователя от новых к старым.

        Cursor содержит пару (updated_at, id), чтобы paging был стабильным,
        включая случаи с одинаковой timestamp.
        """
        page_size = _normalize_limit(
            limit,
            default=DEFAULT_THREADS_PAGE_SIZE,
            maximum=MAX_THREADS_PAGE_SIZE,
        )

        cursor_updated_at: str | None = None
        cursor_id: str | None = None

        if cursor_value:
            cursor_updated_at, cursor_id = _decode_thread_cursor(cursor_value)

        where_sql = "WHERE user_id = ?"
        params: list[Any] = [user_id]

        if cursor_updated_at is not None and cursor_id is not None:
            where_sql += """
                AND (
                    COALESCE(updated_at, created_at) < ?
                    OR (
                        COALESCE(updated_at, created_at) = ?
                        AND id < ?
                    )
                )
            """
            params.extend(
                [
                    cursor_updated_at,
                    cursor_updated_at,
                    cursor_id,
                ]
            )

        connection = await self._database.read_connection()

        cursor = await connection.execute(
            f"""
            SELECT
                t.id,
                t.title,
                t.created_at,
                COALESCE(t.updated_at, t.created_at) AS updated_at,
                (
                    SELECT m.content
                    FROM messages AS m
                    WHERE m.thread_id = t.id
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 1
                ) AS last_message_preview,
                (
                    SELECT m.role
                    FROM messages AS m
                    WHERE m.thread_id = t.id
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 1
                ) AS last_message_role
            FROM threads AS t
            {where_sql}
            ORDER BY
                COALESCE(t.updated_at, t.created_at) DESC,
                t.id DESC
            LIMIT ?
            """,
            [*params, page_size + 1],
        )

        rows = [dict(row) for row in await cursor.fetchall()]
        visible = rows[:page_size]
        has_more = len(rows) > page_size

        items = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "last_message_preview": _truncate_text(
                    row.get("last_message_preview"),
                    limit=300,
                ),
                "last_message_role": row.get("last_message_role"),
            }
            for row in visible
        ]

        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _encode_thread_cursor(
                updated_at=str(last["updated_at"]),
                thread_id=str(last["id"]),
            )

        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def add_message(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        artifact: dict[str, Any] | None = None,
    ) -> str:
        if role not in MESSAGE_ROLES:
            raise ValueError(f"Unsupported message role: {role!r}")

        normalized_content = _required_text(content, "content")
        message_id = f"message-{uuid.uuid4().hex}"
        now = utc_now_iso()

        artifact_json = (
            json.dumps(
                artifact,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if artifact is not None
            else None
        )

        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO messages (
                    id,
                    thread_id,
                    role,
                    content,
                    artifact,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    role,
                    normalized_content,
                    artifact_json,
                    now,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("Failed to append thread message")

            thread_cursor = await connection.execute(
                """
                UPDATE threads
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, thread_id),
            )

            if thread_cursor.rowcount != 1:
                raise ValueError(f"Thread does not exist: {thread_id!r}")

        return message_id

    async def get_messages(
        self,
        *,
        thread_id: str,
        limit: int | None = None,
        before: str | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает страницу истории в порядке от старых к новым — так UI
        может просто дописать результат в existing timeline.

        SQL выбирает последние сообщения DESC, затем Python разворачивает
        только страницу. Cursor привязан к (created_at, id).
        """
        page_size = _normalize_limit(
            limit,
            default=DEFAULT_MESSAGES_PAGE_SIZE,
            maximum=MAX_MESSAGES_PAGE_SIZE,
        )

        before_created_at: str | None = None
        before_id: str | None = None

        if before:
            before_created_at, before_id = _decode_message_cursor(before)

        where_sql = "WHERE thread_id = ?"
        params: list[Any] = [thread_id]

        if before_created_at is not None and before_id is not None:
            where_sql += """
                AND (
                    created_at < ?
                    OR (created_at = ? AND id < ?)
                )
            """
            params.extend(
                [
                    before_created_at,
                    before_created_at,
                    before_id,
                ]
            )

        connection = await self._database.read_connection()

        cursor = await connection.execute(
            f"""
            SELECT
                id,
                role,
                content,
                artifact,
                created_at
            FROM messages
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [*params, page_size + 1],
        )

        rows_desc = [
            _deserialize_message(dict(row))
            for row in await cursor.fetchall()
        ]

        has_more = len(rows_desc) > page_size
        page_desc = rows_desc[:page_size]
        next_before = None

        if has_more and page_desc:
            oldest = page_desc[-1]
            next_before = _encode_message_cursor(
                created_at=str(oldest["created_at"]),
                message_id=str(oldest["id"]),
            )

        items = list(reversed(page_desc))

        return {
            "items": items,
            "next_before": next_before,
            "has_more": has_more,
        }

    async def delete_thread(
        self,
        *,
        thread_id: str,
        user_id: str,
    ) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM threads
                WHERE id = ? AND user_id = ?
                """,
                (thread_id, user_id),
            )

        return cursor.rowcount > 0


def _required_text(value: Any, field_name: str) -> str:
    normalized = _optional_text(value)

    if normalized is None:
        raise ValueError(f"{field_name} must not be empty")

    return normalized


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _normalize_limit(
    value: int | None,
    *,
    default: int,
    maximum: int,
) -> int:
    if value is None:
        return default

    if value < 1:
        raise ValueError("limit must be at least 1")

    return min(value, maximum)


def _truncate_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = " ".join(value.split())
    if not normalized:
        return None

    if len(normalized) <= limit:
        return normalized

    return f"{normalized[: max(1, limit - 1)].rstrip()}…"


def _deserialize_message(record: dict[str, Any]) -> dict[str, Any]:
    artifact = record.get("artifact")

    if not artifact:
        record["artifact"] = None
        return record

    try:
        record["artifact"] = json.loads(artifact)
    except (json.JSONDecodeError, TypeError):
        record["artifact"] = None

    return record


def _encode_thread_cursor(
    *,
    updated_at: str,
    thread_id: str,
) -> str:
    return _encode_cursor(
        {
            "kind": "threads",
            "updated_at": updated_at,
            "id": thread_id,
        }
    )


def _decode_thread_cursor(value: str) -> tuple[str, str]:
    payload = _decode_cursor(value)

    if payload.get("kind") != "threads":
        raise ValueError("Cursor is not a threads cursor")

    updated_at = payload.get("updated_at")
    thread_id = payload.get("id")

    if not isinstance(updated_at, str) or not isinstance(thread_id, str):
        raise ValueError("Invalid threads cursor")

    return updated_at, thread_id


def _encode_message_cursor(
    *,
    created_at: str,
    message_id: str,
) -> str:
    return _encode_cursor(
        {
            "kind": "messages",
            "created_at": created_at,
            "id": message_id,
        }
    )


def _decode_message_cursor(value: str) -> tuple[str, str]:
    payload = _decode_cursor(value)

    if payload.get("kind") != "messages":
        raise ValueError("Cursor is not a messages cursor")

    created_at = payload.get("created_at")
    message_id = payload.get("id")

    if not isinstance(created_at, str) or not isinstance(message_id, str):
        raise ValueError("Invalid messages cursor")

    return created_at, message_id


def _encode_cursor(payload: dict[str, str]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    if not value:
        raise ValueError("Cursor must not be empty")

    padding = "=" * (-len(value) % 4)

    try:
        raw = base64.urlsafe_b64decode(f"{value}{padding}")
        payload = json.loads(raw.decode("utf-8"))
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid cursor") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid cursor")

    return payload