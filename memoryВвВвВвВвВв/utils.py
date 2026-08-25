from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone


def optional_text(value: object) -> str | None:
    """
    Converts a value to stripped text.

    None and blank values become None. Non-string values are converted with
    str() because source imports may contain numeric identifiers and labels.
    """
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def required_text(
    value: object,
    *,
    field_name: str,
) -> str:
    """Returns non-empty stripped text or raises a stable source error."""
    normalized = optional_text(value)

    if normalized is None:
        raise ValueError(
            f"Missing required source field: {field_name}"
        )

    return normalized


def compact_text(value: str) -> str:
    """
    Strips text and collapses any internal whitespace to one regular space.

    Unlike optional_text(), this function intentionally requires str:
    canonical values must not silently accept arbitrary objects.
    """
    if not isinstance(value, str):
        raise TypeError("Expected text value")

    return " ".join(value.strip().split())


def utc_now_iso() -> str:
    """Returns an UTC ISO-8601 timestamp with a trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def datetime_to_iso(
    value: datetime | str | None,
) -> str | None:
    """
    Converts datetime to storage ISO-8601 text.

    Existing strings are preserved because import contracts may deliberately
    retain a non-date sentinel such as ``"неопределенно"`` for assignments.
    Naive datetime remains naive; timezone-aware datetime is converted to UTC.
    """
    if value is None:
        return None

    if isinstance(value, str):
        return value

    if value.tzinfo is None:
        return value.isoformat()

    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def normalize_ids(
    values: Iterable[object],
    *,
    limit: int | None = None,
) -> list[str]:
    """
    Returns unique non-empty IDs in first-seen order.

    It accepts an iterable so repositories can safely normalize lists,
    tuples and sets. Invalid/non-string values are ignored because these
    IDs are internal query candidates, not a user-facing validation input.
    """
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        normalized = value.strip()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

        if limit is not None and len(result) >= limit:
            break

    return result


def placeholders(values: Sequence[object]) -> str:
    """
    Builds parameter placeholders for a non-empty SQL IN clause.

    SQL values must always remain parameters; callers interpolate only this
    generated sequence of question marks, never data values.
    """
    if not values:
        raise ValueError(
            "Cannot build SQL placeholders for an empty sequence"
        )

    return ", ".join("?" for _ in values)