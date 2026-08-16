"""
app/memory/repository/presentations.py

Хранилище презентаций — отдельная persistent-сущность, НЕ LangGraph-артефакт
внутри чекпоинта треда. Причина: "мои презентации"/"общее хранилище" — это
кросс-тредовые и кросс-пользовательские выборки ("все презентации
пользователя X из всех его чатов", "все опубликованные от всех пользователей"),
которые из чекпоинтера пришлось бы добывать полным сканом всех тредов всех
пользователей. Обычная таблица с owner_user_id решает это так же, как уже решено
для threads/messages (см. app/memory/repository/threads.py).

Модель drafts/published:
  - status="draft"    — fields редактируются свободно через update_presentation_fields().
  - status="published" — publish_presentation() ЗАМОРАЖИВАЕТ текущие fields в
    published_snapshot; list_shared_presentations() отдаёт только published_snapshot.
    Дальнейшие правки fields (черновик можно продолжать редактировать и после
    публикации) не меняют то, что видно в общем хранилище — нужен повторный
    publish_presentation(), это осознанное действие, а не побочный эффект правки.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.memory.db.database import get_db


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("fields", "published_snapshot"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = None
    return d


async def create_presentation(
    owner_user_id: str, thread_id: str, fields: Dict[str, Any], analysis_markdown: Optional[str] = None,
) -> str:
    conn = await get_db()
    presentation_id = f"presentation-{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow().isoformat()
    await conn.execute(
        "INSERT INTO presentations "
        "(id, owner_user_id, thread_id, status, fields, analysis_markdown, created_at, updated_at) "
        "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
        (presentation_id, owner_user_id, thread_id, json.dumps(fields, ensure_ascii=False), analysis_markdown, now, now),
    )
    await conn.commit()
    return presentation_id


async def get_presentation(presentation_id: str) -> Optional[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute("SELECT * FROM presentations WHERE id = ?", (presentation_id,))
    row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_my_presentations(owner_user_id: str) -> List[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute(
        "SELECT * FROM presentations WHERE owner_user_id = ? ORDER BY updated_at DESC", (owner_user_id,)
    )
    rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def list_shared_presentations() -> List[Dict[str, Any]]:
    """Общее хранилище: только опубликованные, и только замороженный снапшот —
    см. докстринг модуля про drafts/published."""
    conn = await get_db()
    cur = await conn.execute(
        "SELECT * FROM presentations WHERE status = 'published' ORDER BY published_at DESC"
    )
    rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_presentation_fields(presentation_id: str, owner_user_id: str, fields: Dict[str, Any]) -> bool:
    """Правка черновика. только владелец (owner_user_id в WHERE, не просто id) —
    та же защита, что delete_thread()/delete_message() в threads.py."""
    conn = await get_db()
    cur = await conn.execute(
        "UPDATE presentations SET fields = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?",
        (json.dumps(fields, ensure_ascii=False), datetime.utcnow().isoformat(), presentation_id, owner_user_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def publish_presentation(presentation_id: str, owner_user_id: str) -> bool:
    conn = await get_db()
    cur = await conn.execute(
        "SELECT fields FROM presentations WHERE id = ? AND owner_user_id = ?", (presentation_id, owner_user_id)
    )
    row = await cur.fetchone()
    if row is None:
        return False
    now = datetime.utcnow().isoformat()
    await conn.execute(
        "UPDATE presentations SET status = 'published', published_snapshot = ?, published_at = ?, updated_at = ? "
        "WHERE id = ? AND owner_user_id = ?",
        (row["fields"], now, now, presentation_id, owner_user_id),
    )
    await conn.commit()
    return True


async def unpublish_presentation(presentation_id: str, owner_user_id: str) -> bool:
    """убирает из общего хранилища (status='draft'); published_snapshot не чистим —
    следующий publish_presentation() всё равно перезапишет его текущими fields."""
    conn = await get_db()
    cur = await conn.execute(
        "UPDATE presentations SET status = 'draft', updated_at = ? WHERE id = ? AND owner_user_id = ?",
        (datetime.utcnow().isoformat(), presentation_id, owner_user_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def delete_presentation(presentation_id: str, owner_user_id: str) -> bool:
    conn = await get_db()
    cur = await conn.execute(
        "DELETE FROM presentations WHERE id = ? AND owner_user_id = ?", (presentation_id, owner_user_id)
    )
    await conn.commit()
    return cur.rowcount > 0
