import logging
from typing import Optional
import numpy as np
from app.config import memory_settings
from app.memory.db.database import get_db
from app.memory.repository.embeddings import encode_one

logger = logging.getLogger(__name__)


def pack_vector(vec: np.ndarray) -> bytes:
    """ЕДИНСТВЕННОЕ место с форматом байт для sqlite-vec."""
    return vec.astype(np.float32).tobytes()


async def upsert_incident_vector(incident_number: str, text: Optional[str]) -> None:
    if not memory_settings.sqlite_vec_enabled:
        return
    conn = await get_db()
    cur = await conn.execute("SELECT rowid FROM incidents WHERE number = ?", (incident_number,))
    row = await cur.fetchone()
    if row is None:
        logger.warning("upsert_incident_vector: incident %s not found", incident_number)
        return
    rowid = row[0]
    vec = await encode_one(text or "")
    await conn.execute("INSERT OR REPLACE INTO incident_vec(rowid, embedding) VALUES (?, ?)", (rowid, pack_vector(vec)))
    await conn.commit()
    logger.debug("Upserted vector for incident %s", incident_number)


async def upsert_assignment_vector(assignment_id: int, text: Optional[str]) -> None:
    if not memory_settings.sqlite_vec_enabled:
        return
    conn = await get_db()
    vec = await encode_one(text or "")
    await conn.execute("INSERT OR REPLACE INTO assignment_vec(rowid, embedding) VALUES (?, ?)", (assignment_id, pack_vector(vec)))
    await conn.commit()
    logger.debug("Upserted vector for assignment %d", assignment_id)