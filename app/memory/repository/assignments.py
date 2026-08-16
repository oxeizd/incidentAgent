from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List

import numpy as np

from app.memory.db.database import get_db
from app.memory.repository.vectors import upsert_assignment_vector

logger = logging.getLogger(__name__)

_ASSGN_FIELD_MAP = {
    "incident_id": "incident_id",
    "task": "task",
    "unit": "unit",
    "ior": "ior",
    "responsible": "responsible",
}
_ASSGN_DATE_MAP = {
    "deadline": ("deadline_from", "deadline_to"),
    "date": ("date_from", "date_to"),
}
_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def save_assignments(incident_id: str, assignments: List[Dict[str, Any]]) -> None:
    if not incident_id:
        return
    conn = await get_db()
    await conn.execute("DELETE FROM assignments WHERE incident_id = ?", (incident_id,))
    for item in assignments:
        assignment_text = item.get("assignment", "")
        cur = await conn.execute(
            "INSERT INTO assignments (incident_id, task, unit, assignment, deadline, date, ior, responsible) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                incident_id,
                item.get("task"),
                item.get("unit"),
                assignment_text,
                item.get("deadline"),
                item.get("date"),
                item.get("ior"),
                item.get("responsible"),
            ),
        )
        await upsert_assignment_vector(cur.lastrowid, assignment_text)
    await conn.commit()
    logger.info("Заменено %d поручений для инцидента %s", len(assignments), incident_id)


async def get_assignments_by_incident(incident_id: str) -> List[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute(
        "SELECT id, task, unit, assignment, deadline, date, ior, responsible "
        "FROM assignments WHERE incident_id = ? ORDER BY id ASC",
        (incident_id,),
    )
    return [dict(row) for row in await cur.fetchall()]


async def delete_assignments_by_incident(incident_id: str) -> None:
    conn = await get_db()
    await conn.execute(
        "DELETE FROM assignment_vec WHERE rowid IN (SELECT id FROM assignments WHERE incident_id = ?)",
        (incident_id,),
    )
    await conn.execute("DELETE FROM assignments WHERE incident_id = ?", (incident_id,))
    await conn.commit()
    logger.info("Удалены поручения для инцидента %s", incident_id)


def _date_bound_clause(field: str, value: str, *, is_upper: bool) -> tuple[str, str]:
    if _BARE_DATE_RE.fullmatch(value):
        if is_upper:
            return f" AND {field} < ?", (date.fromisoformat(value) + timedelta(days=1)).isoformat()
        return f" AND {field} >= ?", value
    return (f" AND {field} <= ?", value) if is_upper else (f" AND {field} >= ?", value)


def _apply_field_filters(sql: str, params: list[Any], filters: Dict[str, Any]) -> str:
    for key, column in _ASSGN_FIELD_MAP.items():
        value = filters.get(key)
        if value is not None and value != "":
            sql += f" AND {column} = ?"
            params.append(value)
    return sql


def _apply_date_filters(sql: str, params: list[Any], filters: Dict[str, Any]) -> str:
    for field, (from_key, to_key) in _ASSGN_DATE_MAP.items():
        value_from = filters.get(from_key)
        if value_from:
            fragment, bound = _date_bound_clause(field, value_from, is_upper=False)
            sql += fragment
            params.append(bound)
        value_to = filters.get(to_key)
        if value_to:
            fragment, bound = _date_bound_clause(field, value_to, is_upper=True)
            sql += fragment
            params.append(bound)
    return sql


def _build_where(filters: Dict[str, Any]) -> tuple[str, list[Any]]:
    sql = " WHERE 1=1"
    params: list[Any] = []
    sql = _apply_field_filters(sql, params, filters)
    sql = _apply_date_filters(sql, params, filters)
    return sql, params


async def search_assignments_page(filters: Dict[str, Any]) -> tuple[int, List[Dict[str, Any]]]:
    """Return exact total and one page. SQL filters precede semantic ranking."""
    conn = await get_db()
    limit = int(filters.get("limit", 50))
    offset = int(filters.get("offset", 0))
    text_query = filters.get("text_query")
    where_sql, params = _build_where(filters)

    count_cursor = await conn.execute(f"SELECT COUNT(*) FROM assignments{where_sql}", params)
    total_count = int((await count_cursor.fetchone())[0])
    if total_count == 0:
        return 0, []

    if not text_query:
        sql = (
            "SELECT * FROM assignments"
            f"{where_sql}"
            " ORDER BY COALESCE(deadline, '9999-12-31') ASC, id ASC"
            " LIMIT ? OFFSET ?"
        )
        cursor = await conn.execute(sql, [*params, limit, offset])
        return total_count, [dict(row) for row in await cursor.fetchall()]

    candidates_cursor = await conn.execute(
        f"SELECT id, * FROM assignments{where_sql}",
        params,
    )
    candidates = [dict(row) for row in await candidates_cursor.fetchall()]

    from app.memory.repository.embeddings import encode_one

    query_vector = await encode_one(text_query)
    ids = [candidate["id"] for candidate in candidates]
    placeholders = ",".join("?" * len(ids))
    vector_cursor = await conn.execute(
        f"SELECT rowid, embedding FROM assignment_vec WHERE rowid IN ({placeholders})",
        ids,
    )
    distances: dict[int, float] = {}
    for rowid, blob in await vector_cursor.fetchall():
        vector = np.frombuffer(blob, dtype=np.float32)
        distances[rowid] = float(np.linalg.norm(vector - query_vector))

    candidates.sort(key=lambda item: (distances.get(item["id"], float("inf")), item["id"]))
    page = candidates[offset: offset + limit]
    return total_count, [{**item, "distance": distances.get(item["id"])} for item in page]


async def search_assignments(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Backward-compatible list-only adapter. New code must call search_assignments_page."""
    _, items = await search_assignments_page(filters)
    return items