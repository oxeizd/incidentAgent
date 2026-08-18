from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.loaders.dates import parse_datetime


def map_assignment(
    raw: Mapping[str, Any],
    *,
    fallback_incident_id: str | None = None,
) -> AssignmentUpsert:
    """Map one external assignment payload into a canonical artifact."""
    incident_id = _optional_text(raw.get("incident_id")) or fallback_incident_id

    return AssignmentUpsert(
        id=_optional_text(raw.get("id") or raw.get("assignment_id")),
        incident_id=incident_id,
        ior=_optional_text(raw.get("ior")),
        task=_optional_text(raw.get("task")),
        unit=_optional_text(raw.get("unit")),
        assignment=_required_text(
            raw.get("assignment") or raw.get("text"),
            field_name="assignment",
        ),
        responsible=_optional_text(raw.get("responsible")),
        deadline=parse_datetime(raw.get("deadline")),
        assigned_at=parse_datetime(raw.get("date") or raw.get("assigned_at")),
        status=_optional_text(raw.get("status")),
        source_payload=dict(raw),
    )


def _required_text(value: Any, *, field_name: str) -> str:
    result = _optional_text(value)

    if result is None:
        raise ValueError(f"Missing required source field: {field_name}")

    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None