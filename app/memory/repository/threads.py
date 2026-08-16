import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.memory.db.database import get_db



async def create_thread(thread_id: str, user_id: str) -> None:
    conn = await get_db()
    await conn.execute("INSERT INTO threads (id, user_id, created_at) VALUES (?, ?, ?)",
                        (thread_id, user_id, datetime.utcnow().isoformat()))
    await conn.commit()



async def thread_exists(thread_id: str, user_id: str) -> bool:
    conn = await get_db()
    cur = await conn.execute("SELECT 1 FROM threads WHERE id = ? AND user_id = ?", (thread_id, user_id))
    return await cur.fetchone() is not None



# ДОБАВЛЕНО: нужно для app/ai/graph/orchestrator.py:_on_create_presentation —
# чтобы записать презентацию в app/memory/repository/presentations.py с
# правильным owner_user_id, не протаскивая user_id отдельным полем через
# OrchestratorState/build_initial_state (граф про thread_id знает уже, а
# владельца треда и так хранит эта же таблица, второй источник не нужен).
async def get_thread_owner(thread_id: str) -> Optional[str]:
    conn = await get_db()
    cur = await conn.execute("SELECT user_id FROM threads WHERE id = ?", (thread_id,))
    row = await cur.fetchone()
    return row["user_id"] if row else None



async def get_messages(thread_id: str) -> List[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute(
        "SELECT id, role, content, artifact, created_at FROM messages WHERE thread_id = ? ORDER BY created_at",
        (thread_id,))
    rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("artifact"):
            try:
                d["artifact"] = json.loads(d["artifact"])
            except (json.JSONDecodeError, TypeError):
                d["artifact"] = None
        result.append(d)
    return result



async def add_message(thread_id: str, role: str, content: str, artifact: Optional[Dict] = None) -> None:
    conn = await get_db()
    artifact_json = json.dumps(artifact) if artifact is not None else None
    await conn.execute(
        "INSERT INTO messages (thread_id, role, content, artifact, created_at) VALUES (?, ?, ?, ?, ?)",
        (thread_id, role, content, artifact_json, datetime.utcnow().isoformat()))
    await conn.commit()



async def delete_thread(thread_id: str, user_id: str) -> bool:
    conn = await get_db()
    cur = await conn.execute("DELETE FROM threads WHERE id = ? AND user_id = ?", (thread_id, user_id))
    await conn.commit()
    return cur.rowcount > 0



async def delete_message(message_id: int, thread_id: str) -> bool:
    conn = await get_db()
    cur = await conn.execute("DELETE FROM messages WHERE id = ? AND thread_id = ?", (message_id, thread_id))
    await conn.commit()
    return cur.rowcount > 0



async def get_threads_by_user(user_id: str) -> List[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute("SELECT id, created_at FROM threads WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = await cur.fetchall()
    return [dict(row) for row in rows]
