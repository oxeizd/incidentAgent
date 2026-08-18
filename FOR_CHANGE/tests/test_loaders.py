from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.loaders.assignments import map_assignment
from memory.loaders.incidents import map_incident, parse_resolution_description


def test_maps_incident_and_preserves_raw_resolution_description() -> None:
    raw_resolution_description = (
        "Причина инцидента (подробно): Ошибка маршрутизации.\n"
        "Способ устранения: Переключили резервный маршрут.\n"
        "Влияние: Недоступность части API."
    )

    incident = map_incident(
        {
            "business_id": " INC-1001 ",
            "created_at": "2026-08-18T01:30:00Z",
            "itsm_it_usluga": "Billing",
            "state_code_str": "В работе",
            "impact_custom_service": "да",
            "mttd": "12,5",
            "mttr": 30,
            "resolution_description": raw_resolution_description,
        }
    )

    assert incident.number == "INC-1001"
    assert incident.created_at == datetime(
        2026,
        8,
        18,
        1,
        30,
        tzinfo=timezone.utc,
    )
    assert incident.system_name == "Billing"
    assert incident.impact_custom_service is True
    assert incident.mttd == 12.5
    assert incident.mttr == 30.0

    assert incident.resolution_description == raw_resolution_description

    assert incident.reason_inc == "Ошибка маршрутизации."
    assert incident.solution == "Переключили резервный маршрут."
    assert incident.impact == "Недоступность части API."


def test_maps_assignment_with_soft_incident_reference() -> None:
    assignment = map_assignment(
        {
            "assignment_id": "TASK-42",
            "ior": "ИОР-17",
            "text": " Подготовить план восстановления ",
            "responsible": "Иванов И.И.",
            "deadline": "20.08.2026",
            "date": "2026-08-18",
        },
        fallback_incident_id="INC-1001",
    )

    assert assignment.id == "TASK-42"
    assert assignment.incident_id == "INC-1001"
    assert assignment.assignment == "Подготовить план восстановления"
    assert assignment.deadline == datetime(2026, 8, 20)
    assert assignment.assigned_at == datetime(2026, 8, 18)
    assert assignment.source_payload is not None


def test_assignment_loader_rejects_missing_text() -> None:
    with pytest.raises(
        ValueError,
        match="Missing required source field: assignment",
    ):
        map_assignment(
            {
                "ior": "ИОР-17",
                "responsible": "Иванов И.И.",
            }
        )


def test_parse_resolution_description_returns_empty_for_empty_text() -> None:
    assert parse_resolution_description(None) == {}
    assert parse_resolution_description("") == {}


def test_resolution_parser_supports_multiline_manual_sections() -> None:
    parsed = parse_resolution_description(
        """
        Причина инцидента (подробно):
        Ошибка в правилах маршрутизации.
        Дополнительно некорректно применился конфиг.

        Способ устранения:
        Переключили резервный маршрут.
        Затем исправили конфигурацию.

        Влияние: Частично недоступен API.
        """
    )

    assert parsed == {
        "reason_inc": (
            "Ошибка в правилах маршрутизации.\n"
            "Дополнительно некорректно применился конфиг."
        ),
        "solution": (
            "Переключили резервный маршрут.\n"
            "Затем исправили конфигурацию."
        ),
        "impact": "Частично недоступен API.",
    }


def test_resolution_parser_extracts_manual_start_and_end_time() -> None:
    parsed = parse_resolution_description(
        """
        Фактическое время начала инцидента: 18.08.2026 01:10
        Фактическое время окончания инцидента: 18.08.2026 02:45
        """
    )

    assert parsed == {
        "start_time": "18.08.2026 01:10",
        "end_time": "18.08.2026 02:45",
    }


def test_manual_resolution_values_override_duplicate_raw_fields() -> None:
    incident = map_incident(
        {
            "business_id": "INC-2001",
            "reason_inc": "Устаревшая причина из отдельного поля",
            "solution": "Устаревшее решение из отдельного поля",
            "impact": "Устаревшее влияние из отдельного поля",
            "fact_start_date": "2026-08-18T00:00:00Z",
            "fact_finish_date": "2026-08-18T04:00:00Z",
            "resolution_description": (
                "Причина инцидента: Причина от администратора.\n"
                "Способ устранения: Решение от администратора.\n"
                "Влияние: Влияние от администратора.\n"
                "Фактическое время начала инцидента: 18.08.2026 01:10\n"
                "Фактическое время окончания инцидента: 18.08.2026 02:45"
            ),
        }
    )

    assert incident.reason_inc == "Причина от администратора."
    assert incident.solution == "Решение от администратора."
    assert incident.impact == "Влияние от администратора."
    assert incident.start_time == datetime(2026, 8, 18, 1, 10)
    assert incident.end_time == datetime(2026, 8, 18, 2, 45)