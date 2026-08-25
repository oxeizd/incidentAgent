from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.imports.loaders.dates import parse_datetime
from app.memory.utils import optional_text, required_text


def map_assignment(
    raw: Mapping[str, Any],
    *,
    fallback_incident_id: str | None = None,
) -> AssignmentUpsert:
    """Maps one external assignment payload into a canonical artifact."""
    incident_id = (
        optional_text(raw.get("incident_id"))
        or fallback_incident_id
    )

    return AssignmentUpsert(
        id=optional_text(
            raw.get("id") or raw.get("assignment_id")
        ),
        incident_id=incident_id,
        ior=optional_text(raw.get("ior")),
        task=optional_text(raw.get("task")),
        unit=optional_text(raw.get("unit")),
        assignment=required_text(
            raw.get("assignment") or raw.get("text"),
            field_name="assignment",
        ),
        responsible=optional_text(raw.get("responsible")),
        deadline=(
            parse_datetime(raw.get("deadline"))
            or "неопределенно"
        ),
        assigned_at=parse_datetime(
            raw.get("date") or raw.get("assigned_at")
        ),
        status=optional_text(raw.get("status")),
        source_payload=dict(raw),
    )