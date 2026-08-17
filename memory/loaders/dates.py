from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
)

_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
)


def parse_datetime(value: Any) -> datetime | None:
    """Parse common external date/datetime values without guessing invalid data."""
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime.combine(value, time.min)

    if not isinstance(value, str):
        raise ValueError(f"Expected date/datetime text, got {type(value).__name__}")

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for format_string in _DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized, format_string)
        except ValueError:
            continue

    for format_string in _DATE_FORMATS:
        try:
            return datetime.combine(
                datetime.strptime(normalized, format_string).date(),
                time.min,
            )
        except ValueError:
            continue

    raise ValueError(f"Unsupported date/datetime format: {value!r}")


def parse_bool(value: Any) -> bool:
    """Parse explicit common boolean representations; reject ambiguous input."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in (0, 1):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"1", "true", "yes", "y", "да"}:
            return True

        if normalized in {"0", "false", "no", "n", "нет", ""}:
            return False

    if value is None:
        return False

    raise ValueError(f"Unsupported boolean format: {value!r}")


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric value")

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        normalized = value.strip().replace(",", ".")
        if not normalized:
            return None

        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported numeric format: {value!r}") from exc

    raise ValueError(f"Unsupported numeric type: {type(value).__name__}")


def parse_int(value: Any) -> int | None:
    numeric = parse_float(value)

    if numeric is None:
        return None

    if not numeric.is_integer():
        raise ValueError(f"Expected integer value, got {value!r}")

    return int(numeric)