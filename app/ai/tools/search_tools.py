from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.memory.repository.assignments import search_assignments_page
from app.memory.repository.incidents import search_incidents_page
from app.memory.search_contracts import SearchPage
from app.memory.search_display import get_page_size, project_display

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _validate_datetime_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return value.replace("T", " ")
        except ValueError:
            continue
    raise ValueError(f"Некорректный формат даты {value!r} — используй YYYY-MM-DD или YYYY-MM-DD HH:MM:SS")


class PaginationArgs(BaseModel):
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=200,
        description="Размер страницы. Если не указан — берётся defaults.page_size из data/config/search_output.yaml.",
    )
    offset: int = Field(default=0, ge=0, description="Смещение страницы. Для следующей страницы используй прежний offset + returned_count.")


class SearchIncidentsArgs(PaginationArgs):
    """Точный и семантический поиск инцидентов.

    Для вопросов «сколько за дату/по статусу» используй только точные SQL-фильтры
    и читай ответ только из artifact.total_count. Никогда не называй
    artifact.returned_count общим числом результатов.

    По умолчанию «инциденты за дату» означает start_time (дата регистрации/
    начала). Для закрытых инцидентов используй end_time_from/end_time_to.
    """

    number: Optional[str] = Field(default=None, description="Точный номер инцидента. Если задан, другие фильтры игнорируются.")
    status: Optional[str] = Field(default=None, description="Точный статус, например closed/open.")
    priority_code: Optional[str] = Field(default=None, description="Точный код приоритета.")
    resolution_code: Optional[str] = Field(default=None, description="Точный код решения.")
    registration_basis: Optional[str] = Field(default=None, description="Точное основание регистрации.")
    inc_type: Optional[str] = Field(default=None, description="Точный тип инцидента.")
    stand: Optional[str] = Field(default=None, description="Точный стенд/окружение.")
    system_name: Optional[str] = Field(default=None, description="Точное каноническое название системы из lookup_entity.")
    work_group: Optional[str] = Field(default=None, description="Точное каноническое название рабочей группы.")
    element_name: Optional[str] = Field(default=None, description="Точное каноническое название элемента системы.")
    created_by: Optional[str] = Field(default=None, description="Точное каноническое имя автора.")
    executor_name: Optional[str] = Field(default=None, description="Точное каноническое имя исполнителя.")

    start_time_from: Optional[str] = Field(default=None, description="Начало периода регистрации. YYYY-MM-DD включает сутки с 00:00:00.")
    start_time_to: Optional[str] = Field(default=None, description="Конец периода регистрации. YYYY-MM-DD включает весь календарный день.")
    end_time_from: Optional[str] = Field(default=None, description="Начало периода закрытия.")
    end_time_to: Optional[str] = Field(default=None, description="Конец периода закрытия; YYYY-MM-DD включает весь календарный день.")

    mttd_min: Optional[float] = Field(default=None, ge=0)
    mttd_max: Optional[float] = Field(default=None, ge=0)
    mttr_min: Optional[float] = Field(default=None, ge=0)
    mttr_max: Optional[float] = Field(default=None, ge=0)
    downtime_min: Optional[float] = Field(default=None, ge=0)
    downtime_max: Optional[float] = Field(default=None, ge=0)
    text_query: Optional[str] = Field(default=None, description="Семантический поиск по описанию. Не дублируй сюда точные поля.")

    @field_validator("start_time_from", "start_time_to", "end_time_from", "end_time_to")
    @classmethod
    def check_datetime_format(cls, value: Optional[str]) -> Optional[str]:
        return _validate_datetime_str(value)

    @model_validator(mode="after")
    def check_ranges(self) -> "SearchIncidentsArgs":
        for min_key, max_key in (("mttd_min", "mttd_max"), ("mttr_min", "mttr_max"), ("downtime_min", "downtime_max")):
            minimum = getattr(self, min_key)
            maximum = getattr(self, max_key)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{min_key} must not exceed {max_key}")
        return self


class SearchAssignmentsArgs(PaginationArgs):
    """Точный и семантический поиск поручений.

    Поле date означает дату создания/фиксации поручения в исходных данных;
    deadline означает срок исполнения. Для вопроса «какие поручения со сроком
    за май» используй deadline_from/deadline_to, а не date_from/date_to.
    """

    incident_id: Optional[str] = Field(default=None, description="Точный номер/ID связанного инцидента.")
    task: Optional[str] = Field(default=None, description="Точная категория/задача поручения.")
    unit: Optional[str] = Field(default=None, description="Точное подразделение.")
    ior: Optional[str] = Field(default=None, description="Точное значение ИОР.")
    responsible: Optional[str] = Field(default=None, description="Точное каноническое имя ответственного.")
    deadline_from: Optional[str] = Field(default=None, description="Начало периода срока исполнения.")
    deadline_to: Optional[str] = Field(default=None, description="Конец периода срока исполнения; YYYY-MM-DD включает весь день.")
    date_from: Optional[str] = Field(default=None, description="Начало периода даты поручения.")
    date_to: Optional[str] = Field(default=None, description="Конец периода даты поручения; YYYY-MM-DD включает весь день.")
    text_query: Optional[str] = Field(default=None, description="Семантический поиск по тексту поручения.")

    @field_validator("deadline_from", "deadline_to", "date_from", "date_to")
    @classmethod
    def check_datetime_format(cls, value: Optional[str]) -> Optional[str]:
        return _validate_datetime_str(value)


def _applied_filters(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key not in {"limit", "offset"} and value is not None and value != ""
    }


def _build_page(
    *,
    entity: str,
    total_count: int,
    items: list[dict[str, Any]],
    limit: int,
    offset: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    page = SearchPage(
        entity=entity,
        total_count=total_count,
        returned_count=len(items),
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total_count,
        applied_filters=_applied_filters(filters),
        items=items,
        display=project_display(entity, items),
    )
    return page.model_dump(mode="json")


@tool(args_schema=SearchIncidentsArgs, response_format="content_and_artifact")
async def search_incidents_tool(**kwargs: Any) -> Tuple[str, dict[str, Any]]:
    """Ищет инциденты и возвращает полный page artifact.

    `total_count` — точное число ВСЕХ совпадений до LIMIT/OFFSET.
    `items` — записи текущей страницы.
    `display` — отображаемые поля строго по data/config/search_output.yaml.
    """
    limit = get_page_size(kwargs.get("limit"))
    filters = {key: value for key, value in kwargs.items() if value is not None}
    filters["limit"] = limit
    total_count, items = await search_incidents_page(filters)
    artifact = _build_page(
        entity="incidents",
        total_count=total_count,
        items=items,
        limit=limit,
        offset=int(filters.get("offset", 0)),
        filters=filters,
    )
    return (
        f"Инциденты: всего {total_count}; показано {len(items)}; offset={artifact['offset']}; has_more={artifact['has_more']}",
        artifact,
    )


@tool(args_schema=SearchAssignmentsArgs, response_format="content_and_artifact")
async def search_assignments_tool(**kwargs: Any) -> Tuple[str, dict[str, Any]]:
    """Ищет поручения и возвращает полный page artifact.

    `total_count` — точное число ВСЕХ совпадений до LIMIT/OFFSET.
    `items` — записи текущей страницы.
    `display` — отображаемые поля строго по data/config/search_output.yaml.
    """
    limit = get_page_size(kwargs.get("limit"))
    filters = {key: value for key, value in kwargs.items() if value is not None}
    filters["limit"] = limit
    total_count, items = await search_assignments_page(filters)
    artifact = _build_page(
        entity="assignments",
        total_count=total_count,
        items=items,
        limit=limit,
        offset=int(filters.get("offset", 0)),
        filters=filters,
    )
    return (
        f"Поручения: всего {total_count}; показано {len(items)}; offset={artifact['offset']}; has_more={artifact['has_more']}",
        artifact,
    )