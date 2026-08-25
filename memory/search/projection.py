from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from app.memory.search.contracts import (
    DisplayColumn,
    DisplaySchema,
    PreviewRow,
)


NULL_VALUE = "—"
ELLIPSIS = "…"


def project_row(
    *,
    entity_id: str,
    payload: dict[str, Any],
    schema: DisplaySchema,
) -> PreviewRow:
    """
    Projects one domain payload to a UI-safe display row.

    Только поля из DisplaySchema попадают в результат. Исходный payload,
    включая source_payload или внутренние DB поля, не раскрывается.
    """
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
    if _is_empty_value(value):
        return NULL_VALUE

    if column.format == "date":
        text = _format_date(value)
    elif column.format == "datetime":
        text = _format_datetime(value)
    elif column.format == "number":
        text = _format_number(value)
    else:
        text = str(value)

    normalized = _normalize_text(text)

    if not normalized:
        return NULL_VALUE

    return _truncate_text(
        normalized,
        limit=column.truncate,
    )


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    return False


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, str):
        normalized = value.strip()

        if not normalized:
            return ""

        parsed = _parse_iso_date(normalized)
        if parsed is not None:
            return parsed.strftime("%d.%m.%Y")

        return normalized

    return str(value)


def _format_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")

    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")

    if isinstance(value, str):
        normalized = value.strip()

        if not normalized:
            return ""

        parsed = _parse_iso_datetime(normalized)
        if parsed is not None:
            return parsed.strftime("%d.%m.%Y %H:%M")

        return _format_date(normalized)

    return str(value)


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            return NULL_VALUE

        return f"{value:.2f}".rstrip("0").rstrip(".")

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return ""

        parsed = _parse_number(normalized)
        if parsed is None:
            return normalized

        return _format_number(parsed)

    return str(value)


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    normalized = value

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_number(value: str) -> float | None:
    normalized = value.replace(" ", "").replace(",", ".")

    try:
        parsed = float(normalized)
    except ValueError:
        return None

    return parsed if math.isfinite(parsed) else None


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _truncate_text(
    value: str,
    *,
    limit: int | None,
) -> str:
    if limit is None or len(value) <= limit:
        return value

    if limit < 2:
        return ELLIPSIS

    return f"{value[: limit - 1].rstrip()}{ELLIPSIS}"