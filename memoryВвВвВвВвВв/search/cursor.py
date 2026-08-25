from __future__ import annotations

import base64
import binascii
import json

from pydantic import BaseModel, ConfigDict, Field


class ResultCursor(BaseModel):
    """Opaque pagination cursor for one persisted search result."""

    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(min_length=1, max_length=200)
    after_position: int = Field(ge=-1)


def encode_cursor(cursor: ResultCursor) -> str:
    raw = cursor.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> ResultCursor:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Cursor must not be empty")

    normalized = value.strip()
    padding = "=" * (-len(normalized) % 4)

    try:
        raw = base64.urlsafe_b64decode(
            f"{normalized}{padding}"
        )
        payload = json.loads(raw.decode("utf-8"))
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid cursor") from exc

    try:
        return ResultCursor.model_validate(payload)
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc