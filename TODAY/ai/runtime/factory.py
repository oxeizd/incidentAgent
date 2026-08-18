from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.types import Send
from pydantic import ValidationError

from app.ai.registry.payloads import validate_payload
from app.ai.registry.workflows import WORKFLOW_REGISTRY
from app.ai.schemas.worker import WorkerState


@dataclass(frozen=True, slots=True)
class SpawnError:
    """
    Контролируемая ошибка старта worker-а.

    Это не exception: orchestrator превращает её в безопасный user response,
    а не падает целиком.
    """

    reason: str
    missing_constraint: str | None = None
    suggestion: str | None = None

    def to_user_message(self) -> str:
        return self.suggestion or self.reason


def spawn_worker(
    *,
    kind: str,
    input_context: dict[str, Any],
    state: dict[str, Any],
) -> Send | SpawnError:
    """
    Проверяет workflow contract и создаёт начальный WorkerState.

    Возвращает LangGraph Send(entry_node, state), чтобы orchestrator мог
    запустить compiled subgraph с правильной entry node.
    """
    spec = WORKFLOW_REGISTRY.get(kind)

    if spec is None:
        return SpawnError(
            reason=f"Unknown workflow kind: {kind}",
        )

    can_run, reason = spec.validate_preconditions(
        state,
        input_context,
    )

    if not can_run:
        return SpawnError(
            reason=f"Cannot start workflow {kind!r}",
            missing_constraint=reason,
            suggestion=reason,
        )

    raw_payload = input_context.get("payload") or {}

    try:
        payload = validate_payload(
            kind,
            raw_payload,
        )
    except (ValidationError, ValueError) as exc:
        return SpawnError(
            reason=f"Invalid payload for workflow {kind!r}",
            suggestion=str(exc),
        )

    worker_id = f"{kind}-{uuid.uuid4().hex[:12]}"
    seed_history = input_context.get("seed_history") or []

    worker: WorkerState = {
        "worker_id": worker_id,
        "kind": kind,
        "parent_worker_id": input_context.get("parent_worker_id"),
        "history": list(seed_history),
        "input_context": {
            key: value
            for key, value in input_context.items()
            if key not in {"payload", "seed_history"}
        },
        "payload": payload,
        "status": "running",
        "rounds": 0,
        "max_rounds": spec.default_max_rounds,
        "summary_for_parent": None,
        "produced_artifact_refs": [],
    }

    return Send(spec.entry_node, worker)