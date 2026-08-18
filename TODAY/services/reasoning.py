from __future__ import annotations

from typing import Any, Optional

try:
    from langgraph.config import get_stream_writer
except Exception:
    get_stream_writer = None


# NodeCtx.log() is intentionally a user-safe operational channel. Never pass
# model hidden reasoning, prompts, credentials, database values or raw errors.
def emit_reasoning(
    node: str,
    message: str,
    worker_id: Optional[str] = None,
    *,
    stage: Optional[str] = None,
    visibility: str = "user",
    **extra: Any,
) -> None:
    if get_stream_writer is None:
        return
    try:
        writer = get_stream_writer()
    except Exception:
        return

    writer({
        "type": "agent.reasoning",
        "node": node,
        "stage": stage or node,
        "worker_id": worker_id,
        "message": message,
        "visibility": visibility,
        **extra,
    })