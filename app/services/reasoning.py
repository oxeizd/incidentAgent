from __future__ import annotations
from typing import Optional

try:
    from langgraph.config import get_stream_writer
except Exception:
    get_stream_writer = None


def emit_reasoning(node: str, message: str, worker_id: Optional[str] = None, **extra) -> None:
    if get_stream_writer is None:
        return
    try:
        writer = get_stream_writer()
    except Exception:
        return
    writer({"type": "reasoning", "node": node, "worker_id": worker_id, "message": message, **extra})