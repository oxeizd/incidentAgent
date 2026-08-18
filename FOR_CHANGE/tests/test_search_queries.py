from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from memory.search.queries import AssignmentSearchQuery, IncidentSearchQuery


def test_assignment_query_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as error:
        AssignmentSearchQuery.model_validate(
            {
                "ior_code": "ИОР-17",
            }
        )

    assert "ior_code" in str(error.value)
    assert "Extra inputs are not permitted" in str(error.value)


def test_assignment_query_rejects_invalid_deadline_range() -> None:
    with pytest.raises(ValidationError) as error:
        AssignmentSearchQuery.model_validate(
            {
                "deadline_from": "2026-09-01",
                "deadline_to": "2026-08-01",
            }
        )

    assert "deadline_from must not be greater than deadline_to" in str(error.value)


def test_incident_query_rejects_invalid_numeric_range() -> None:
    with pytest.raises(ValidationError) as error:
        IncidentSearchQuery(
            mttr_min=120,
            mttr_max=30,
        )

    assert "mttr_min must not be greater than mttr_max" in str(error.value)


def test_incident_query_rejects_negative_metric() -> None:
    with pytest.raises(ValidationError) as error:
        IncidentSearchQuery(mttd_min=-1)

    assert "greater than or equal to 0" in str(error.value)


def test_assignment_query_normalizes_dates_to_json() -> None:
    query = AssignmentSearchQuery(
        ior="ИОР-17",
        responsible="Иванов И.И.",
        deadline_from=date(2026, 8, 1),
        deadline_to=date(2026, 8, 31),
    )

    assert query.to_normalized_dict() == {
        "ior": "ИОР-17",
        "responsible": "Иванов И.И.",
        "deadline_from": "2026-08-01",
        "deadline_to": "2026-08-31",
    }


def test_incident_query_normalizes_numeric_filters() -> None:
    query = IncidentSearchQuery(
        system_name="Billing",
        status="В работе",
        mttr_min=10,
        mttr_max=120,
    )

    assert query.to_normalized_dict() == {
        "system_name": "Billing",
        "status": "В работе",
        "mttr_min": 10.0,
        "mttr_max": 120.0,
    }