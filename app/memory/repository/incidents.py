from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from app.memory.db.database import get_db
from app.memory.repository.vectors import upsert_incident_vector

logger = logging.getLogger(__name__)

_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_FIELD_MAP = {
    "work_group": "work_group",
    "element_name": "element_name",
    "system_name": "system_name",
    "created_by": "created_by",
    "executor_name": "executor_name",
    "status": "status",
    "priority_code": "priority_code",
    "resolution_code": "resolution_code",
    "registration_basis": "registration_basis",
    "inc_type": "inc_type",
    "stand": "stand",
}

_DATE_MAP = {
    "start_time": ("start_time_from", "start_time_to"),
    "end_time": ("end_time_from", "end_time_to"),
}

_NUMERIC_RANGES = (
    ("mttd", "min"), ("mttr", "min"), ("downtime", "min"),
    ("mttd", "max"), ("mttr", "max"), ("downtime", "max"),
)


def _date_bound_clause(field: str, value: str, *, is_upper: bool) -> tuple[str, str]:
    """Calendar-day upper bound is exclusive: 2026-05-05 -> < 2026-05-06.

    This prevents a datetime column from excluding incidents after midnight on
    the requested day, and works for SQLite ISO-like TEXT datetime storage.
    """
    if _BARE_DATE_RE.fullmatch(value):
        if is_upper:
            return f" AND {field} < ?", (date.fromisoformat(value) + timedelta(days=1)).isoformat()
        return f" AND {field} >= ?", value
    return (f" AND {field} <= ?", value) if is_upper else (f" AND {field} >= ?", value)


async def save_incident(data: Dict[str, Any]) -> Optional[str]:
    number = data.get("number")
    if not number:
        logger.warning("save_incident: missing 'number'")
        return None

    conn = await get_db()
    columns = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    sql = f"INSERT OR REPLACE INTO incidents ({columns}) VALUES ({placeholders})"
    try:
        await conn.execute(sql, list(data.values()))
        await conn.commit()
    except Exception as exc:
        logger.error("Error saving incident %s: %s", number, exc)
        return None

    await upsert_incident_vector(number, data.get("reason_inc"))
    return number


async def get_incident_by_number(number: str) -> Optional[Dict[str, Any]]:
    conn = await get_db()
    cur = await conn.execute("SELECT * FROM incidents WHERE number = ?", (number,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def update_incident(number: str, updates: Dict[str, Any]) -> bool:
    if not updates:
        return True
    conn = await get_db()
    set_clause = ", ".join([f"{key} = ?" for key in updates])
    try:
        await conn.execute(
            f"UPDATE incidents SET {set_clause} WHERE number = ?",
            [*updates.values(), number],
        )
        await conn.commit()
    except Exception as exc:
        logger.error("Error updating incident %s: %s", number, exc)
        return False

    if "reason_inc" in updates:
        await upsert_incident_vector(number, updates["reason_inc"])
    return True


async def delete_incident(number: str) -> bool:
    """ПО ЯВНОМУ РЕШЕНИЮ: assignments НЕ удаляются каскадно."""
    conn = await get_db()
    try:
        await conn.execute(
            "DELETE FROM incident_vec WHERE rowid IN (SELECT rowid FROM incidents WHERE number = ?)",
            (number,),
        )
        await conn.execute("DELETE FROM incidents WHERE number = ?", (number,))
        await conn.commit()
        logger.info("Incident %s deleted", number)
        return True
    except Exception as exc:
        logger.error("Error deleting incident %s: %s", number, exc)
        return False


def _apply_field_filters(sql: str, params: list[Any], filters: Dict[str, Any]) -> str:
    for key, column in _FIELD_MAP.items():
        value = filters.get(key)
        if value is not None and value != "":
            sql += f" AND {column} = ?"
            params.append(value)
    return sql


def _apply_date_filters(sql: str, params: list[Any], filters: Dict[str, Any]) -> str:
    for field, (from_key, to_key) in _DATE_MAP.items():
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


def _apply_numeric_filters(sql: str, params: list[Any], filters: Dict[str, Any]) -> str:
    for field, suffix in _NUMERIC_RANGES:
        value = filters.get(f"{field}_{suffix}")
        if value is None or value == "":
            continue
        operator = ">=" if suffix == "min" else "<="
        sql += f" AND {field} {operator} ?"
        params.append(float(value))
    return sql


def _build_where(filters: Dict[str, Any]) -> tuple[str, list[Any]]:
    if filters.get("number"):
        return " WHERE number = ?", [filters["number"]]

    sql = " WHERE 1=1"
    params: list[Any] = []
    sql = _apply_field_filters(sql, params, filters)
    sql = _apply_date_filters(sql, params, filters)
    sql = _apply_numeric_filters(sql, params, filters)
    return sql, params


async def search_incidents_page(filters: Dict[str, Any]) -> tuple[int, List[Dict[str, Any]]]:
    """Return exact total before pagination and one deterministic page.

    If text_query exists, SQL filters define the complete candidate set first;
    embeddings rank that set second. Therefore exact/date filters never lose
    matching records because of a vector top-K cutoff.
    """
    conn = await get_db()
    limit = int(filters.get("limit", 50))
    offset = int(filters.get("offset", 0))
    text_query = filters.get("text_query")
    where_sql, params = _build_where(filters)

    count_cursor = await conn.execute(f"SELECT COUNT(*) FROM incidents{where_sql}", params)
    total_count = int((await count_cursor.fetchone())[0])
    if total_count == 0:
        return 0, []

    if not text_query:
        sql = (
            "SELECT * FROM incidents"
            f"{where_sql}"
            " ORDER BY start_time DESC, number ASC"
            " LIMIT ? OFFSET ?"
        )
        cur = await conn.execute(sql, [*params, limit, offset])
        return total_count, [dict(row) for row in await cur.fetchall()]

    candidates_cursor = await conn.execute(
        f"SELECT rowid, * FROM incidents{where_sql}",
        params,
    )
    candidates = [dict(row) for row in await candidates_cursor.fetchall()]

    from app.memory.repository.embeddings import encode_one

    query_vector = await encode_one(text_query)
    rowids = [candidate["rowid"] for candidate in candidates]
    placeholders = ",".join("?" * len(rowids))
    vector_cursor = await conn.execute(
        f"SELECT rowid, embedding FROM incident_vec WHERE rowid IN ({placeholders})",
        rowids,
    )
    distances: dict[int, float] = {}
    for rowid, blob in await vector_cursor.fetchall():
        vector = np.frombuffer(blob, dtype=np.float32)
        distances[rowid] = float(np.linalg.norm(vector - query_vector))

    candidates.sort(key=lambda item: (distances.get(item["rowid"], float("inf")), item.get("number", "")))
    page = candidates[offset: offset + limit]
    return total_count, [
        {key: value for key, value in item.items() if key != "rowid"} | {"distance": distances.get(item["rowid"])}
        for item in page
    ]


async def search_incidents(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Backward-compatible list-only adapter. New code must call search_incidents_page."""
    _, items = await search_incidents_page(filters)
    return items
