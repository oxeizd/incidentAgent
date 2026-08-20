from __future__ import annotations

import json
import time
import uuid
from typing import Any


def new_stream_id() -> str:
    return f"conversation-{uuid.uuid4().hex}"


def unix_now() -> int:
    return int(time.time())


def turn_event(
    *,
    stream_id: str,
    created: int,
    event_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": stream_id,
        "object": "conversation.turn.event",
        "created": created,
        "type": event_type,
        "data": data,
    }


def status_event(
    *,
    stream_id: str,
    created: int,
    status: str,
) -> dict[str, Any]:
    return turn_event(
        stream_id=stream_id,
        created=created,
        event_type="status",
        data={
            "status": status,
        },
    )


def result_event(
    *,
    stream_id: str,
    created: int,
    message: str,
    task: dict[str, Any] | None,
    suspended_task: dict[str, Any] | None,
    pending_interaction: dict[str, Any] | None,
    artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    return turn_event(
        stream_id=stream_id,
        created=created,
        event_type="result",
        data={
            "message": message,
            "task": task,
            "suspended_task": suspended_task,
            "pending_interaction": pending_interaction,
            "artifact": artifact,
            "finish_reason": (
                "requires_input"
                if pending_interaction is not None
                else "stop"
            ),
        },
    )


def error_event(
    *,
    stream_id: str,
    created: int,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    return turn_event(
        stream_id=stream_id,
        created=created,
        event_type="error",
        data={
            "message": message,
            "retryable": retryable,
        },
    )


def as_sse_data(
    payload: dict[str, Any],
) -> dict[str, str]:
    return {
        "data": json.dumps(
            payload,
            ensure_ascii=False,
        ),
    }


def done_sse_event() -> dict[str, str]:
    return {
        "data": "[DONE]",
    }