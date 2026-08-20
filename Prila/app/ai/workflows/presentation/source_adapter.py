from __future__ import annotations

from typing import Any

from app.ai.runtime.services import get_memory
from app.ai.schemas.conversation import (
    IncidentRef,
    IncidentReportRef,
)
from app.ai.workflows.presentation.contracts import (
    PresentationSource,
)
from app.memory.artifacts.presentations.document import (
    DASH,
    PresentationDocument,
)


class PresentationSourceError(RuntimeError):
    """Контролируемая ошибка загрузки источника презентации."""


async def load_incident_source(
    *,
    incident_ref: IncidentRef,
) -> PresentationSource:
    """
    Загружает LLM-safe incident snapshot через MemoryFacade.

    Поля presentation не извлекаются Python-эвристиками: raw incident
    передаётся Collector-у, который сам решает, какие документные поля
    подтверждены исходными данными.
    """
    number = (incident_ref.number or "").strip()

    if not number:
        raise PresentationSourceError(
            "Для презентации не указан номер инцидента."
        )

    incident = await get_memory().get_incident_for_agent(
        number=number,
    )

    if incident is None:
        raise PresentationSourceError(
            f"Инцидент {number} не найден."
        )

    return PresentationSource(
        kind="incident",
        incident_ref=incident_ref,
        loaded_data={
            "incident": incident,
        },
    )


def load_description_source(
    *,
    raw_description: str,
) -> PresentationSource:
    """
    Свободное описание — допустимый источник.

    Collector сам извлекает подтверждённые fields и при необходимости
    задаёт вопрос. Никаких промежуточных Python extraction rules.
    """
    normalized = raw_description.strip()

    if not normalized:
        raise PresentationSourceError(
            "Не передано описание для презентации."
        )

    return PresentationSource(
        kind="description",
        raw_description=normalized,
        loaded_data={
            "description": normalized,
        },
    )


def load_report_source(
    *,
    report_ref: IncidentReportRef,
    report_sections: dict[str, Any],
) -> PresentationSource:
    """
    Готовая RCA-справка — source для presentation.

    Передаём sections в Collector без ручной переработки. Это позволяет LLM
    использовать богатую новую структуру `_rca`, а не быть привязанной к
    старому `_rca_input` формату.
    """
    if not report_sections:
        raise PresentationSourceError(
            "RCA-справка не содержит данных для презентации."
        )

    return PresentationSource(
        kind="report",
        report_ref=report_ref,
        loaded_data={
            "report_sections": report_sections,
        },
    )


def merge_presentation_fields(
    *,
    current: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge только фактически переданных LLM update fields.

    Отсутствие поля не меняет current. Пустые строки и "—" не затирают
    существующую подтверждённую информацию; final normalization остаётся
    зоной PresentationDocument.
    """
    merged = dict(current)

    for key, value in update.items():
        if value in (None, "", DASH, [], {}, ()):
            continue

        merged[key] = value

    return merged


def build_presentation_preview(
    document: PresentationDocument,
) -> str:
    """
    Текстовый preview перед persistence.

    Это не HTML и не дублирование текущего renderer-а. Он позволяет
    пользователю проверить существенные данные будущей presentation до
    create_presentation().
    """
    lines = [
        "### Preview презентации",
        "",
        f"**Инцидент:** {document.number}",
        f"**Кратко:** {document.brief}",
        f"**Система/юнит:** {document.unit}",
        f"**Причина:** {document.cause}",
        f"**Влияние:** {document.impact}",
    ]

    if document.timeline:
        lines.extend(
            [
                "",
                "**Хронология:**",
                *[
                    f"- {event}"
                    for event in document.timeline
                ],
            ]
        )

    if document.assignments:
        lines.extend(
            [
                "",
                (
                    "**Системные меры:** "
                    f"{len(document.assignments)}"
                ),
            ]
        )

        for index, assignment in enumerate(
            document.assignments,
            start=1,
        ):
            lines.append(
                f"{index}. {assignment.title}"
            )

    if document.analysis_markdown:
        lines.extend(
            [
                "",
                "**RCA-анализ:** будет включён.",
            ]
        )

    lines.extend(
        [
            "",
            "Сохранить презентацию?",
        ]
    )

    return "\n".join(lines)