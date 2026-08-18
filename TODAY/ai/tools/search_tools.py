from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.ai.runtime.services import get_memory


class StructuredIncidentSearchArgs(BaseModel):
    """
    Фильтры exact structured поиска инцидентов.

    Неизвестные поля запрещены. Семантический запрос должен идти через
    search_similar_incidents, а не попадать в structured filters.
    """

    model_config = ConfigDict(extra="forbid")

    number: str | None = None
    status: str | None = None
    priority_code: str | None = None
    system_name: str | None = None
    work_group: str | None = None
    element_name: str | None = None
    executor_name: str | None = None
    stand: str | None = None

    start_time_from: str | None = Field(
        default=None,
        description="Дата начала периода в формате YYYY-MM-DD.",
    )
    start_time_to: str | None = Field(
        default=None,
        description="Дата конца периода в формате YYYY-MM-DD.",
    )

    end_time_from: str | None = Field(
        default=None,
        description="Дата начала периода закрытия в формате YYYY-MM-DD.",
    )
    end_time_to: str | None = Field(
        default=None,
        description="Дата конца периода закрытия в формате YYYY-MM-DD.",
    )

    mttd_min: float | None = Field(default=None, ge=0)
    mttd_max: float | None = Field(default=None, ge=0)
    mttr_min: float | None = Field(default=None, ge=0)
    mttr_max: float | None = Field(default=None, ge=0)
    downtime_min: float | None = Field(default=None, ge=0)
    downtime_max: float | None = Field(default=None, ge=0)

    limit: int = Field(default=5, ge=1, le=10)


class StructuredAssignmentSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    incident_id: str | None = None
    ior: str | None = None
    unit: str | None = None
    responsible: str | None = None
    status: str | None = None

    deadline_from: str | None = Field(
        default=None,
        description="Дата в формате YYYY-MM-DD.",
    )
    deadline_to: str | None = Field(
        default=None,
        description="Дата в формате YYYY-MM-DD.",
    )
    assigned_at_from: str | None = Field(
        default=None,
        description="Дата в формате YYYY-MM-DD.",
    )
    assigned_at_to: str | None = Field(
        default=None,
        description="Дата в формате YYYY-MM-DD.",
    )

    limit: int = Field(default=5, ge=1, le=10)


class SemanticSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=2, max_length=10_000)
    limit: int = Field(default=5, ge=1, le=10)


@tool(args_schema=StructuredIncidentSearchArgs)
async def retrieve_incidents_for_agent(
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Read-only точный SQL поиск инцидентов для agent/RCA.

    Не создаёт message, search artifact или snapshot результата для UI.
    Используй для номера инцидента, статуса, системы, группы, дат, метрик.
    """
    payload = StructuredIncidentSearchArgs.model_validate(kwargs)
    values = payload.model_dump(exclude={"limit"}, exclude_none=True)

    return await get_memory().retrieve_incidents_for_agent(
        filters=values,
        limit=payload.limit,
    )


@tool(args_schema=StructuredAssignmentSearchArgs)
async def retrieve_assignments_for_agent(
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Read-only точный SQL поиск поручений для agent/RCA.

    Не создаёт message, search artifact или snapshot результата для UI.
    """
    payload = StructuredAssignmentSearchArgs.model_validate(kwargs)
    values = payload.model_dump(exclude={"limit"}, exclude_none=True)

    return await get_memory().retrieve_assignments_for_agent(
        filters=values,
        limit=payload.limit,
    )


@tool(args_schema=SemanticSearchArgs)
async def find_similar_incidents_for_agent(
    query_text: str,
    limit: int = 5,
) -> dict[str, Any]:
    """
    Read-only semantic search похожих инцидентов по причине/ai_description.

    Не используй для точного номера, ФИО, системы, даты или статуса.
    """
    return await get_memory().find_similar_incidents_for_agent(
        query_text=query_text,
        limit=limit,
    )


@tool(args_schema=SemanticSearchArgs)
async def find_similar_assignments_for_agent(
    query_text: str,
    limit: int = 5,
) -> dict[str, Any]:
    """
    Read-only semantic search похожих системных поручений.
    """
    return await get_memory().find_similar_assignments_for_agent(
        query_text=query_text,
        limit=limit,
    )