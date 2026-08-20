from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.services.entity_resolver import lookup_entities


@tool
async def lookup_entity(
    raw_value: str,
) -> list[dict[str, Any]]:
    """
    Ищет canonical значения в справочнике систем, рабочих групп,
    исполнителей и элементов.

    Вызывай для любого пользовательского названия системы, сервиса,
    команды, исполнителя или компонента, если оно не выглядит как точный
    canonical идентификатор.

    Не вызывай для номера инцидента, дат, статусов, приоритетов и
    свободного текстового описания проблемы.

    Результат может содержать кандидатов разных типов (system_name,
    work_group, executor_name, element_name) — сам оцени, какой тип
    подходит по смыслу запроса пользователя.
    """
    return await lookup_entities(
        raw_value,
        top_k=5,
    )