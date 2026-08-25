from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.memory.search.contracts import DisplaySchema


_PROFILE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "profile": "incidents.chat_preview.v1",
        "title": "Инциденты",
        "columns": [
            {"key": "number", "label": "Номер"},
            {"key": "priority_code", "label": "Приоритет"},
            {"key": "system_name", "label": "Система"},
            {
                "key": "start_time",
                "label": "Начало",
                "format": "datetime",
            },
            {
                "key": "downtime",
                "label": "Время простоя",
            },
            {"key": "reason_inc", "label": "Причина"},
            {"key": "solution", "label": "Решение"},
        ],
    },
    {
        "profile": "incidents.table.v1",
        "title": "Инциденты",
        "columns": [
            {"key": "number", "label": "Номер"},
            {"key": "status", "label": "Статус"},
            {"key": "priority_code", "label": "Приоритет"},
            {"key": "system_name", "label": "Система"},
            {"key": "element_name", "label": "Элемент"},
            {"key": "work_group", "label": "Рабочая группа"},
            {"key": "executor_name", "label": "Исполнитель"},
            {
                "key": "start_time",
                "label": "Начало",
                "format": "datetime",
            },
            {
                "key": "end_time",
                "label": "Окончание",
                "format": "datetime",
            },
            {
                "key": "downtime",
                "label": "Время простоя",
            },
            {"key": "reason_inc", "label": "Причина"},
            {"key": "solution", "label": "Решение"},
        ],
    },
    {
        "profile": "assignments.chat_preview.v1",
        "title": "Поручения",
        "columns": [
            {"key": "ior", "label": "ИОР"},
            {
                "key": "assignment",
                "label": "Поручение",
                "truncate": 180,
            },
            {
                "key": "responsible",
                "label": "Ответственный",
            },
            {
                "key": "deadline",
                "label": "Срок",
                "format": "date",
            },
        ],
    },
    {
        "profile": "assignments.table.v1",
        "title": "Поручения",
        "columns": [
            {"key": "id", "label": "ID"},
            {"key": "incident_id", "label": "Инцидент"},
            {"key": "ior", "label": "ИОР"},
            {
                "key": "task",
                "label": "Задача",
                "truncate": 140,
            },
            {
                "key": "assignment",
                "label": "Поручение",
                "truncate": 300,
            },
            {
                "key": "responsible",
                "label": "Ответственный",
            },
            {"key": "unit", "label": "Подразделение"},
            {
                "key": "deadline",
                "label": "Срок",
                "format": "date",
            },
            {"key": "status", "label": "Статус"},
        ],
    },
)

SEARCH_DISPLAY_PROFILES: dict[str, DisplaySchema] = {
    definition["profile"]: DisplaySchema.model_validate(definition)
    for definition in _PROFILE_DEFINITIONS
}


def get_display_profile(profile_name: str) -> DisplaySchema:
    """
    Возвращает независимую typed-copy display profile.

    Callers не могут случайно изменить глобальный registry через mutation
    columns или model_copy(update=...), что важно для persisted snapshots.
    """
    try:
        profile = SEARCH_DISPLAY_PROFILES[profile_name]
    except KeyError as exc:
        available = ", ".join(sorted(SEARCH_DISPLAY_PROFILES))
        raise ValueError(
            f"Unknown search display profile: {profile_name}. "
            f"Available: {available}"
        ) from exc

    return profile.model_copy(deep=True)


def list_display_profiles() -> tuple[str, ...]:
    """Стабильный список зарегистрированных display profile names."""
    return tuple(sorted(SEARCH_DISPLAY_PROFILES))