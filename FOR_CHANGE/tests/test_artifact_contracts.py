from __future__ import annotations

import pytest
from pydantic import ValidationError

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert


def test_assignment_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AssignmentUpsert(
            assignment="Подготовить отчёт",
            unexpected_field="value",
        )


def test_assignment_rejects_blank_text() -> None:
    with pytest.raises(ValidationError, match="assignment must not be blank"):
        AssignmentUpsert(assignment="   ")


def test_incident_normalizes_number_and_optional_text() -> None:
    incident = IncidentUpsert(
        number="  INC-1001  ",
        status="  В работе ",
        system_name="   ",
    )

    assert incident.number == "INC-1001"
    assert incident.status == "В работе"
    assert incident.system_name is None


def test_incident_rejects_invalid_month() -> None:
    with pytest.raises(ValidationError):
        IncidentUpsert(
            number="INC-1001",
            month_created=13,
        )