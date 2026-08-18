from __future__ import annotations

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.embeddings.documents import (
    assignment_embedding_text,
    incident_embedding_text,
)


def test_incident_embedding_prefers_ai_description() -> None:
    incident = IncidentUpsert(
        number="INC-1001",
        ai_description="Краткое AI-описание причины сбоя маршрутизации.",
        reason_inc="Ручное описание причины.",
        system_name="Billing",
        executor_name="Иванов И.И.",
    )

    assert incident_embedding_text(incident) == (
        "Краткое AI-описание причины сбоя маршрутизации."
    )


def test_incident_embedding_falls_back_to_reason() -> None:
    incident = IncidentUpsert(
        number="INC-1002",
        reason_inc="Ошибка применения конфигурации маршрутизации.",
        system_name="Billing",
        executor_name="Петров П.П.",
    )

    assert incident_embedding_text(incident) == (
        "Ошибка применения конфигурации маршрутизации."
    )


def test_incident_without_ai_description_or_reason_is_not_vectorized() -> None:
    incident = IncidentUpsert(
        number="INC-1003",
        system_name="Billing",
        description="Служебное описание без зафиксированной причины.",
    )

    assert incident_embedding_text(incident) is None


def test_incident_embedding_ignores_metadata() -> None:
    incident = IncidentUpsert(
        number="INC-1004",
        reason_inc="Потеря соединения между сервисами.",
        system_name="Critical Billing System",
        work_group="Эксплуатация",
        executor_name="Иванов Иван Иванович",
        status="В работе",
        description="Система недоступна с 10:20.",
    )

    assert incident_embedding_text(incident) == (
        "Потеря соединения между сервисами."
    )


def test_assignment_embedding_contains_only_assignment_text() -> None:
    assignment = AssignmentUpsert(
        id="TASK-1",
        incident_id="INC-1001",
        ior="ИОР-17",
        responsible="Иванов И.И.",
        assignment="Подготовить план восстановления API Gateway.",
    )

    assert assignment_embedding_text(assignment) == (
        "Подготовить план восстановления API Gateway."
    )