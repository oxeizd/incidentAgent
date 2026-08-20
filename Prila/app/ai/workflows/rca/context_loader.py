from __future__ import annotations

from typing import Any

from app.ai.runtime.services import get_memory
from app.ai.schemas.conversation import (
    IncidentRef,
    IncidentReportRef,
)
from app.ai.workflows.rca.contracts import RCAInput


class RCAContextLoadError(RuntimeError):
    """
    Контролируемая ошибка загрузки RCA source.

    Workflow показывает понятное пользовательское сообщение, не технический
    traceback. LLM gate не вызывается с искусственно придуманным context.
    """


async def load_from_incident(
    *,
    incident_ref: IncidentRef,
) -> RCAInput:
    """
    Загружает полный incident context по стабильному номеру.

    MemoryFacade остаётся единственной точкой доступа к данным и ownership.
    `IncidentRef.id` допускает внутренний ID, но для текущего API нужен
    incident number; поэтому ref обязательно должен содержать number.
    """
    number = (incident_ref.number or "").strip()

    if not number:
        raise RCAContextLoadError(
            "Для RCA не указан номер инцидента."
        )

    incident = await get_memory().get_incident_for_agent(
        number=number,
    )

    if incident is None:
        raise RCAContextLoadError(
            f"Инцидент {number} не найден."
        )

    return RCAInput(
        source_kind="incident",
        incident_ref=incident_ref,
        source_data={
            "incident": incident,
        },
    )


def load_from_description(
    *,
    raw_description: str,
) -> RCAInput:
    """
    Подготавливает RCA source по свободному описанию.

    Здесь ничего не извлекаем Python-кодом. Gate LLM выделит симптомы,
    evidence, гипотезы и нужные уточнения.
    """
    normalized = raw_description.strip()

    if not normalized:
        raise RCAContextLoadError(
            "Не передано описание проблемы для RCA."
        )

    return RCAInput(
        source_kind="description",
        raw_description=normalized,
        source_data={
            "description": normalized,
        },
    )


def load_from_report(
    *,
    report_ref: IncidentReportRef,
    report_sections: dict[str, Any],
) -> RCAInput:
    """
    Строит source для reanalyze из уже загруженного artifact snapshot.

    Artifact lookup намеренно выполняется в report adapter/workflow:
    artifacts текущего диалога находятся в ConversationState, а не в
    MemoryFacade. Этот loader получает уже проверенные sections и не
    ищет report по произвольному ID.
    """
    if not report_sections:
        raise RCAContextLoadError(
            "В RCA-справке нет данных для повторного анализа."
        )

    return RCAInput(
        source_kind="report",
        report_ref=report_ref,
        source_data={
            "report_sections": report_sections,
        },
    )


def load_from_search_result(
    *,
    search_result_id: str,
    selected_incident: IncidentRef | None = None,
) -> RCAInput:
    """
    Временный contract для RCA после Search.

    В нормальном flow Search executor сначала должен получить конкретный
    IncidentRef. Если его ещё нет, RCA workflow не запускает gate, а
    создаёт interaction выбора инцидента.

    Когда будет добавлен MemoryFacade.get_search_result_for_agent(), сюда
    можно добавить scoped loading сохранённого search result по ID без
    изменения RCAInput или RCA gate.
    """
    normalized_id = search_result_id.strip()

    if not normalized_id:
        raise RCAContextLoadError(
            "Не передан результат поиска для RCA."
        )

    if selected_incident is None:
        return RCAInput(
            source_kind="search_result",
            search_result_id=normalized_id,
            source_data={},
        )

    return RCAInput(
        source_kind="incident",
        incident_ref=selected_incident,
        source_data={
            "selected_from_search_result": normalized_id,
        },
    )