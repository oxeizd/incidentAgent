from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import Optional, Union, TYPE_CHECKING
from pydantic import ValidationError
from langgraph.types import Send
from app.ai.schemas.worker import WorkerState
from app.ai.registry.workflows import WORKFLOW_REGISTRY
from app.ai.registry.payloads import validate_payload

if TYPE_CHECKING:
    from app.ai.schemas.orchestrator import OrchestratorState


@dataclass
class SpawnError:
    reason: str
    missing_constraint: Optional[str] = None
    suggestion: Optional[str] = None

    def to_user_message(self) -> str:
        return self.suggestion or self.reason


def spawn_worker(kind: str, input_context: dict, state: "OrchestratorState") -> Union[Send, SpawnError]:
    spec = WORKFLOW_REGISTRY.get(kind)
    if not spec:
        return SpawnError(reason=f"Unknown workflow kind: {kind}")

    can_run, error_msg = spec.validate_preconditions(state, input_context)
    if not can_run:
        return SpawnError(reason=f"Cannot start '{kind}'", missing_constraint=error_msg, suggestion=error_msg)

    payload = input_context.get("payload", {})
    try:
        validated_payload = validate_payload(kind, payload)
    except (ValidationError, ValueError) as e:
        return SpawnError(reason=f"Invalid payload for '{kind}'", suggestion=str(e))

    worker_id = f"{kind}-{uuid.uuid4().hex[:8]}"
    seed_history = input_context.get("seed_history", [])
    initial: WorkerState = {
        "worker_id": worker_id, "kind": kind,
        "parent_worker_id": input_context.get("parent_worker_id"),
        "history": list(seed_history),
        "input_context": {k: v for k, v in input_context.items() if k not in ("payload", "seed_history")},
        "payload": validated_payload, "status": "running", "rounds": 0,
        "max_rounds": spec.default_max_rounds, "summary_for_parent": None, "produced_artifact_refs": [],
    }
    return Send(spec.entry_node, initial)