from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from memory.db.connection import Database

MESSAGE_ROLES = frozenset({"user", "assistant", "system", "tool"})

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ThreadRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_thread(self, *, user_id: str, title: str | None = None) -> str:
        thread_id = f"thread-{uuid.uuid4().hex}"

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO threads (id, user_id, title, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, user_id, title, utc_now_iso()),
            )

        return thread_id

    async def thread_belongs_to_user(self, *, thread_id: str, user_id: str) -> bool:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            "SELECT 1 FROM threads WHERE id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return await cursor.fetchone() is not None

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

        if not content.strip():
            raise ValueError("Message content must not be empty")
        
        message_id = f"message-{uuid.uuid4().hex}"

        artifact_json = (
            json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
            if artifact is not None
            else None
        )

        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, artifact, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    role,
                    content,
                    artifact_json,
                    utc_now_iso(),
                ),
            )

        return message_id

    async def get_messages(self, thread_id: str) -> list[dict[str, Any]]:
        connection = await self._database.read_connection()

        cursor = await connection.execute(
            """
            SELECT id, role, content, artifact, created_at
            FROM messages
            WHERE thread_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (thread_id,),
        )
        rows = await cursor.fetchall()

        return [_deserialize_message(dict(row)) for row in rows]


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