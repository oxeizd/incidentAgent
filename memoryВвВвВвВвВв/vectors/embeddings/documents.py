from __future__ import annotations

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.incidents.contracts import IncidentUpsert


def incident_embedding_text(
    incident: IncidentUpsert,
) -> str | None:
    """
    Semantic document for incident similarity.

    В embedding попадает содержательный технический контекст инцидента:
    описание, причина, решение/устранение, влияние и краткое AI-summary.

    Порядок приоритета отражает надёжность источника: исходный текст,
    введённый администратором, важнее короткого LLM-generated summary.

    Номера, статусы, исполнители, даты, система и прочие точные атрибуты
    не включаются: их нужно применять через structured filters, а не через
    semantic similarity.
    """
    parts = _labeled_parts(
        (
            ("Описание инцидента", incident.description),
            ("Причина инцидента", incident.reason_inc),
            ("Решение", incident.solution),
            (
                "Описание устранения",
                incident.resolution_description,
            ),
            ("Влияние", incident.impact),
            ("Краткое AI-описание", incident.ai_description),
        )
    )

    if not parts:
        return None

    return "\n\n".join(
        (
            "Тип записи: инцидент.",
            *parts,
        )
    )


def assignment_embedding_text(
    assignment: AssignmentUpsert,
) -> str:
    """
    Semantic document for assignment similarity.

    Текст поручения является обязательным. Задача и подразделение
    добавляются, только если заданы, чтобы лучше различать похожие
    поручения без включения идентификаторов и персональных данных.
    """
    assignment_text = _optional_non_empty(assignment.assignment)

    if assignment_text is None:
        raise ValueError("Assignment cannot be vectorized without text")

    parts = _labeled_parts(
        (
            ("Поручение", assignment_text),
            ("Задача", assignment.task),
            ("Подразделение", assignment.unit),
        )
    )

    return "\n\n".join(
        (
            "Тип записи: поручение.",
            *parts,
        )
    )


def _labeled_parts(
    values: tuple[tuple[str, str | None], ...],
) -> list[str]:
    result: list[str] = []

    for label, raw_value in values:
        value = _optional_non_empty(raw_value)

        if value is not None:
            result.append(f"{label}:\n{value}")

    return result


def _optional_non_empty(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None