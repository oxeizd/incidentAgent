from __future__ import annotations

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert


def incident_embedding_text(incident: IncidentUpsert) -> str | None:
    """
    Text used only for incident semantic similarity.

    Priority:
    1. AI-generated description when available.
    2. Manually supplied incident reason.
    3. No vector at all.

    Deliberately excludes IDs, people, systems, statuses, dates, resolution
    text and other metadata. Those are searched only with structured SQL.
    """
    return _optional_non_empty(incident.ai_description) or _optional_non_empty(
        incident.reason_inc
    )


def assignment_embedding_text(assignment: AssignmentUpsert) -> str:
    """
    Text used only for assignment semantic similarity.

    AssignmentUpsert guarantees the source field is non-empty.
    """
    text = _optional_non_empty(assignment.assignment)

    if text is None:
        raise ValueError("Assignment cannot be vectorized without text")

    return text


def _optional_non_empty(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None