from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal


EVENT_SCHEMA_VERSION = 1


class AgentEventType(StrEnum):
    RUN_STARTED = "run.started"
    AGENT_STATUS = "agent.status"
    AGENT_REASONING = "agent.reasoning"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    INTERRUPT_REQUESTED = "interrupt.requested"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_FINAL = "message.final"
    ARTIFACT_CREATED = "artifact.created"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


EventVisibility = Literal["user", "developer"]


@dataclass(frozen=True)
class AgentEvent:
    """Transport-neutral event. Every external streaming protocol is an adapter over this model."""

    type: AgentEventType
    thread_id: str
    run_id: str
    sequence: int
    data: dict[str, Any]
    worker_id: str | None = None
    node: str | None = None
    stage: str | None = None
    visibility: EventVisibility = "user"
    event_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            object.__setattr__(self, "event_id", f"evt_{uuid.uuid4().hex}")
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": self.event_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "type": self.type.value,
            "visibility": self.visibility,
            "worker_id": self.worker_id,
            "node": self.node,
            "stage": self.stage,
            "data": self.data,
        }