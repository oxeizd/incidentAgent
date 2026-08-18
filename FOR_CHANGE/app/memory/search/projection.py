from __future__ import annotations

from datetime import date, datetime
from typing import Any

from memory.search.contracts import DisplayColumn, DisplaySchema, PreviewRow


NULL_VALUE = "—"


def project_row(
    *,
    entity_id: str,
    payload: dict[str, Any],
    schema: DisplaySchema,
) -> PreviewRow:
    return PreviewRow(
        entity_id=entity_id,
        values={
            column.key: format_display_value(
                payload.get(column.key),
                column=column,
            )
            for column in schema.columns
        },
    )


def format_display_value(
    value: Any,
    *,
    column: DisplayColumn,
) -> str:
    if value is None or value == "":
        return NULL_VALUE

    text: str

    if column.format == "date":
        text = _format_date(value)
    elif column.format == "datetime":
        text = _format_datetime(value)
    elif column.format == "number":
        text = _format_number(value)
    else:
        text = str(value)

    text = " ".join(text.split())

    if column.truncate is not None and len(text) > column.truncate:
        return f"{text[: max(1, column.truncate - 1)].rstrip()}…"

    return text


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).strftime("%d.%m.%Y")
        except ValueError:
            return value

    return str(value)


def _format_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")

    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")

        try:
            return datetime.fromisoformat(normalized).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return _format_date(value)

    return str(value)


def _format_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")

    return str(value)