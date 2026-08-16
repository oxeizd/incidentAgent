"""
app/ai/runtime/outcomes.py

Wave 7: typed vocabulary for worker node outcomes. LangGraph state remains a
plain dict for checkpoint/API compatibility; WorkerOutcome only centralises
status/error construction so new node authors do not spread arbitrary status
strings and error dict shapes across the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

from langchain_core.messages import AIMessage


class WorkerStatus(StrEnum):
    RUNNING = "running"
    AWAITING_USER_INPUT = "awaiting_user_input"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEVIATED = "deviated"
    VALIDATED = "validated"
    LOW_CONFIDENCE_STOP = "low_confidence_stop"


@dataclass(frozen=True)
class WorkerError:
    code: str
    message: str

    def to_state(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class WorkerOutcome:
    status: WorkerStatus
    message: str
    summary: Optional[dict[str, Any]] = None
    payload_update: Optional[dict[str, Any]] = None
    error: Optional[WorkerError] = None

    def to_state_update(self, *, payload: dict[str, Any], rounds: int) -> dict:
        update: dict[str, Any] = {
            "payload": payload,
            "status": self.status.value,
            "history": [AIMessage(content=self.message)],
        }
        if self.summary is not None:
            update["summary_for_parent"] = self.summary
        if self.error is not None:
            update["error"] = self.error.to_state()
        if self.status in {WorkerStatus.RUNNING, WorkerStatus.AWAITING_USER_INPUT}:
            update["rounds"] = rounds + 1
        return update
