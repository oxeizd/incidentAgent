from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d.%m",
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

_INDEFINITE_DATETIME_VALUES = frozenset(
    {
        "бессрочно",
        "бессрочно.",
        "indefinitely",
        "tbd",
        "to be determined",
        "b.d.",
        "b.d",
        "б.д.",
        "б.д",
        "после создания реестра",
        "после создания реестра.",
    }
)

_ISO_FRACTION_RE = re.compile(
    r"^(?P<prefix>.+?\.\d{1,})(?P<suffix>Z|[+-]\d{2}:\d{2})?$"
)


def parse_datetime(value: Any) -> datetime | None:
    """
    Parse common external date/datetime values.

    ISO datetimes с timezone сохраняют offset. Если fraction содержит
    более шести знаков, обрезается только fraction, а suffix timezone
    остаётся неизменным.
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime.combine(value, time.min)

    if not isinstance(value, str):
        raise ValueError(
            "Expected date/datetime text, got "
            f"{type(value).__name__}"
        )

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.casefold() in _INDEFINITE_DATETIME_VALUES:
        return None

    normalized = _normalize_iso_datetime(normalized)

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
                datetime.strptime(
                    normalized,
                    format_string,
                ).date(),
                time.min,
            )
        except ValueError:
            continue

    raise ValueError(
        f"Unsupported date/datetime format: {value!r}"
    )


def parse_bool(value: Any) -> bool:
    """Parse explicit boolean values and reject ambiguous representations."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in (0, 1):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().casefold()

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
            raise ValueError(
                f"Unsupported numeric format: {value!r}"
            ) from exc

    raise ValueError(
        f"Unsupported numeric type: {type(value).__name__}"
    )


def parse_int(value: Any) -> int | None:
    numeric = parse_float(value)

    if numeric is None:
        return None

    if not numeric.is_integer():
        raise ValueError(
            f"Expected integer value, got {value!r}"
        )

    return int(numeric)


def _normalize_iso_datetime(value: str) -> str:
    """
    Normalizes ISO-8601 details accepted by source exports.

    - trailing Z becomes +00:00;
    - fractional seconds are truncated to 6 digits;
    - timezone suffix is preserved.
    """
    normalized = value

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    if "T" not in normalized or "." not in normalized:
        return normalized

    match = _ISO_FRACTION_RE.match(normalized)
    if match is None:
        return normalized

    prefix = match.group("prefix")
    suffix = match.group("suffix") or ""

    head, fraction = prefix.rsplit(".", maxsplit=1)

    if len(fraction) <= 6:
        return normalized

    return f"{head}.{fraction[:6]}{suffix}"