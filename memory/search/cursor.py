from __future__ import annotations

import base64
import json

from pydantic import BaseModel, ConfigDict, Field


class ResultCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(min_length=1)
    after_position: int = Field(ge=-1)


def encode_cursor(cursor: ResultCursor) -> str:
    raw = cursor.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> ResultCursor:
    if not value:
        raise ValueError("Cursor must not be empty")

    padding = "=" * (-len(value) % 4)

    try:
        raw = base64.urlsafe_b64decode(f"{value}{padding}")
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc

    return ResultCursor.model_validate(payload)