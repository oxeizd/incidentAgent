from __future__ import annotations

from memory.search.contracts import DisplaySchema

SEARCH_DISPLAY_PROFILES: dict[str, DisplaySchema] = {
    "incidents.chat_preview.v1": DisplaySchema.model_validate(
        {
            "profile": "incidents.chat_preview.v1",
            "title": "Инциденты",
            "columns": [
                {"key": "number", "label": "Номер"},
                {"key": "status", "label": "Статус"},
                {"key": "priority_code", "label": "Приоритет"},
                {"key": "system_name", "label": "Система"},
                {
                    "key": "start_time",
                    "label": "Начало",
                    "format": "datetime",
                },
                {
                    "key": "reason_inc",
                    "label": "Причина",
                    "truncate": 180,
                },
            ],
        }
    ),
    "incidents.table.v1": DisplaySchema.model_validate(
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
            ],
        }
    ),
    "assignments.chat_preview.v1": DisplaySchema.model_validate(
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
                {"key": "responsible", "label": "Ответственный"},
                {
                    "key": "deadline",
                    "label": "Срок",
                    "format": "date",
                },
            ],
        }
    ),
    "assignments.table.v1": DisplaySchema.model_validate(
        {
            "profile": "assignments.table.v1",
            "title": "Поручения",
            "columns": [
                {"key": "id", "label": "ID"},
                {"key": "incident_id", "label": "Инцидент"},
                {"key": "ior", "label": "ИОР"},
                {"key": "task", "label": "Задача", "truncate": 140},
                {
                    "key": "assignment",
                    "label": "Поручение",
                    "truncate": 300,
                },
                {"key": "responsible", "label": "Ответственный"},
                {"key": "unit", "label": "Подразделение"},
                {
                    "key": "deadline",
                    "label": "Срок",
                    "format": "date",
                },
                {"key": "status", "label": "Статус"},
            ],
        }
    ),
}


def get_display_profile(profile_name: str) -> DisplaySchema:
    try:
        return SEARCH_DISPLAY_PROFILES[profile_name]
    except KeyError as exc:
        available = ", ".join(sorted(SEARCH_DISPLAY_PROFILES))
        raise ValueError(
            f"Unknown search display profile: {profile_name}. "
            f"Available: {available}"
        ) from exc