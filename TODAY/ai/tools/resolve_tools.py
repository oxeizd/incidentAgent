from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.services.entity_resolver import lookup_entities


@tool
async def lookup_entity(
    raw_value: str,
) -> list[dict[str, Any]]:
    """
    Ищет каноническое значение только для каталожных полей:

    - system_name;
    - work_group;
    - executor_name;
    - element_name;
    - created_by.

    Вызывай, только если в пользовательском запросе уже выделено конкретное
    неточное название системы, команды, элемента либо ФИО.

    Не вызывай для:
    - номера инцидента;
    - дат и периодов;
    - статусов и приоритетов;
    - числовых метрик;
    - свободного описания инцидента;
    - слов «инциденты», «поручения», «найди», «покажи».
    """
    return await lookup_entities(
        raw_value,
        top_k=5,
    )