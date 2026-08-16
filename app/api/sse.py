from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.api.safe_errors import user_fallback_for_exception
from app.api.schemas import interrupt_to_tool_call
from app.ai.runtime.events import AgentEvent, AgentEventType

logger = logging.getLogger(__name__)


class EventFactory:
    def __init__(self, thread_id: str, run_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.run_id = run_id or f"run_{uuid.uuid4().hex}"
        self._sequence = 0

    def make(
        self,
        event_type: AgentEventType,
        data: dict[str, Any],
        *,
        worker_id: str | None = None,
        node: str | None = None,
        stage: str | None = None,
        visibility: str = "user",
    ) -> AgentEvent:
        self._sequence += 1
        return AgentEvent(
            type=event_type,
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=self._sequence,
            data=data,
            worker_id=worker_id,
            node=node,
            stage=stage,
            visibility=visibility,  # type: ignore[arg-type]
        )


def sse_event(event: str, data: Any, *, event_id: str | None = None) -> dict[str, str]:
    payload = {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}
    if event_id:
        payload["id"] = event_id
    return payload


def versioned_event(event: AgentEvent) -> dict[str, str]:
    return sse_event(event.type.value, event.to_dict(), event_id=event.event_id)


async def stream_graph_events(
    graph,
    graph_input,
    config,
    *,
    thread_id: str,
    run_id: str | None = None,
    suppress: bool = False,
) -> AsyncIterator[dict[str, str]]:
    """Versioned custom SSE stream. Legacy `message`, `reasoning`, `error`, `done` are retained."""
    events = EventFactory(thread_id=thread_id, run_id=run_id)
    started_at = time.monotonic()
    interrupted = False
    final_message: str | None = None
    final_artifact: dict[str, Any] | None = None
    final_tool_calls: list[dict[str, Any]] | None = None
    last_stage: str | None = None

    started = events.make(AgentEventType.RUN_STARTED, {"status": "running"})
    yield versioned_event(started)

    try:
        async for namespace, mode, chunk in graph.astream(
            graph_input,
            config=config,
            stream_mode=["updates", "custom"],
            subgraphs=True,
        ):
            if mode == "custom":
                custom = chunk if isinstance(chunk, dict) else {"message": str(chunk)}
                if custom.get("visibility", "user") != "user":
                    continue
                event_type = AgentEventType.AGENT_REASONING if custom.get("type") == "agent.reasoning" else AgentEventType.AGENT_STATUS
                progress = events.make(
                    event_type,
                    {"message": custom.get("message", "Выполняю задачу")},
                    worker_id=custom.get("worker_id"),
                    node=custom.get("node"),
                    stage=custom.get("stage") or custom.get("node"),
                )
                last_stage = progress.stage
                yield versioned_event(progress)
                # Compatibility with current custom frontend.
                yield sse_event("reasoning", progress.to_dict(), event_id=progress.event_id)
                continue

            for node_name, update in (chunk or {}).items():
                if node_name == "__interrupt__":
                    if interrupted or suppress:
                        continue
                    interrupted = True
                    interrupt_payload = _interrupt_payload(update)
                    final_message = interrupt_payload.get("question") or "Уточните запрос"
                    final_tool_calls = [interrupt_to_tool_call(interrupt_payload).model_dump()]
                    interrupt = events.make(
                        AgentEventType.INTERRUPT_REQUESTED,
                        {"question": final_message, "tool_calls": final_tool_calls},
                        worker_id=interrupt_payload.get("worker_id"),
                        stage=last_stage,
                    )
                    yield versioned_event(interrupt)
                    continue

                if not isinstance(update, dict):
                    continue
                artifacts = update.get("artifacts")
                artifact_id = update.get("current_artifact_id")
                if artifacts and artifact_id and artifact_id in artifacts:
                    final_artifact = artifacts[artifact_id]
                    artifact_event = events.make(
                        AgentEventType.ARTIFACT_CREATED,
                        {"artifact_id": artifact_id, "artifact": final_artifact},
                        node=node_name,
                        stage=last_stage,
                    )
                    yield versioned_event(artifact_event)

                messages = update.get("messages")
                if messages:
                    last = messages[-1] if isinstance(messages, list) else messages
                    content = getattr(last, "content", None)
                    if content:
                        final_message = content

    except Exception as exc:
        safe = user_fallback_for_exception(exc, stage=last_stage)
        failed = events.make(AgentEventType.RUN_FAILED, safe.to_dict(), stage=last_stage)
        yield versioned_event(failed)
        yield sse_event("error", safe.to_dict(), event_id=failed.event_id)
        yield sse_event("done", {"ok": False, "run_id": events.run_id})
        return

    if final_message:
        final = events.make(
            AgentEventType.MESSAGE_FINAL,
            {
                "content": final_message,
                "artifact": final_artifact,
                "awaiting_input": interrupted,
                "tool_calls": final_tool_calls if interrupted else None,
            },
            stage=last_stage,
        )
        yield versioned_event(final)
        yield sse_event("message", final.data, event_id=final.event_id)

    completed = events.make(
        AgentEventType.RUN_COMPLETED,
        {
            "status": "awaiting_input" if interrupted else "completed",
            "duration_ms": round((time.monotonic() - started_at) * 1000),
        },
        stage=last_stage,
    )
    yield versioned_event(completed)
    yield sse_event("done", {"ok": True, "run_id": events.run_id}, event_id=completed.event_id)


def _interrupt_payload(update: Any) -> dict[str, Any]:
    items = update if isinstance(update, (list, tuple)) else [update]
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            return value
    return {"question": "Уточните запрос", "type": "question", "kind": "question"}