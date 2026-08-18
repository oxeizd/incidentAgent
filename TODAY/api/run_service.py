from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.ai.schemas.orchestrator import build_initial_state
from app.api.safe_errors import user_fallback_for_exception
from app.api.schemas import (
    CreateRunRequest,
    RunError,
    RunEvent,
    RunEventType,
    ToolOutput,
    interaction_to_tool_call,
)
from app.memory.facade import MemoryFacade


class RunConflictError(Exception):
    """Для одного thread разрешён только один одновременно исполняющийся run."""


class InvalidToolOutputError(Exception):
    """Frontend прислал output не для текущего native graph interrupt."""


_MAX_TRACKED_LOCKS = 4_096


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def graph_config(
    thread_id: str,
) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": 50,
    }


class ConversationRunService:
    """
    HTTP/SSE adapter между API и compiled LangGraph conversation graph.

    Ответственность:
    - thread-level locking;
    - persistence chat message history через MemoryFacade;
    - native LangGraph interrupt → UI tool_call;
    - UI tool output → Command(resume=...);
    - SSE events.

    Не классифицирует intent и не реализует business workflow.
    """

    def __init__(
        self,
        *,
        graph: Any,
        memory: MemoryFacade,
    ) -> None:
        self._graph = graph
        self._memory = memory

        self._in_flight: set[str] = set()
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def _lock_for(
        self,
        thread_id: str,
    ) -> asyncio.Lock:
        existing = self._locks.get(thread_id)

        if existing is not None:
            self._locks.move_to_end(thread_id)
            return existing

        lock = asyncio.Lock()
        self._locks[thread_id] = lock

        while len(self._locks) > _MAX_TRACKED_LOCKS:
            self._locks.popitem(last=False)

        return lock

    def validate_request(
        self,
        *,
        thread_id: str,
        request: CreateRunRequest,
    ) -> None:
        if thread_id in self._in_flight:
            raise RunConflictError(
                "Thread is already processing a run"
            )

        if request.text is None and not request.tool_outputs:
            raise ValueError(
                "Provide text or tool_outputs"
            )

    async def get_state(
        self,
        *,
        thread_id: str,
    ) -> dict[str, Any]:
        snapshot = await self._graph.aget_state(
            graph_config(thread_id),
        )

        values = (
            snapshot.values
            if isinstance(snapshot.values, dict)
            else {}
        )

        interaction = _extract_interrupt_payload(snapshot)

        return {
            "awaiting_input": interaction is not None,
            "pending_interaction": interaction,
            "values": values,
            "next_nodes": list(
                getattr(snapshot, "next", ()) or ()
            ),
        }

    async def create_run(
        self,
        *,
        thread_id: str,
        user_id: str,
        request: CreateRunRequest,
    ) -> AsyncIterator[RunEvent]:
        if thread_id in self._in_flight:
            raise RunConflictError(
                "Thread is already processing a run"
            )

        self._in_flight.add(thread_id)

        try:
            async with self._lock_for(thread_id):
                async for event in self._run_locked(
                    thread_id=thread_id,
                    user_id=user_id,
                    request=request,
                ):
                    yield event
        finally:
            self._in_flight.discard(thread_id)

    async def _run_locked(
        self,
        *,
        thread_id: str,
        user_id: str,
        request: CreateRunRequest,
    ) -> AsyncIterator[RunEvent]:
        run_id = f"run_{uuid.uuid4().hex}"
        sequence = 0
        started_at = time.monotonic()

        def emit(
            event_type: RunEventType,
            data: dict[str, Any],
        ) -> RunEvent:
            nonlocal sequence

            sequence += 1

            return RunEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                thread_id=thread_id,
                run_id=run_id,
                sequence=sequence,
                timestamp=utc_now_iso(),
                type=event_type,
                data=data,
            )

        snapshot = await self._graph.aget_state(
            graph_config(thread_id),
        )
        pending_interrupt = _extract_interrupt_payload(snapshot)

        graph_input: Any
        persisted_user_content: str

        if pending_interrupt is not None:
            graph_input = self._resolve_interrupt_output(
                interrupt_payload=pending_interrupt,
                request=request,
            )
            persisted_user_content = _resume_to_text(
                graph_input.resume,
            )
        else:
            if request.text is None:
                raise ValueError(
                    "Thread is not awaiting a tool output"
                )

            graph_input = await self._new_turn_input(
                snapshot=snapshot,
                user_id=user_id,
                thread_id=thread_id,
                text=request.text,
            )
            persisted_user_content = request.text

        await self._memory.add_thread_message_for_user(
            user_id=user_id,
            thread_id=thread_id,
            role="user",
            content=persisted_user_content,
        )

        yield emit(
            RunEventType.RUN_STARTED,
            {
                "status": "running",
            },
        )

        final_message: str | None = None
        final_artifact: dict[str, Any] | None = None
        requires_action: dict[str, Any] | None = None

        try:
            async for item in self._graph.astream(
                graph_input,
                config=graph_config(thread_id),
                stream_mode=["updates", "custom"],
                subgraphs=True,
            ):
                namespace, mode, chunk = _unpack_stream_item(item)

                if mode == "custom":
                    progress = _normalize_progress(chunk)

                    if progress is not None:
                        yield emit(
                            RunEventType.RUN_PROGRESS,
                            progress,
                        )

                    continue

                for update in _iter_state_updates(chunk):
                    messages = update.get("messages")

                    if isinstance(messages, list) and messages:
                        content = getattr(
                            messages[-1],
                            "content",
                            None,
                        )

                        if (
                            isinstance(content, str)
                            and content.strip()
                        ):
                            final_message = content.strip()

                    artifacts = update.get("artifacts")
                    current_artifact_id = update.get(
                        "current_artifact_id"
                    )

                    if (
                        isinstance(artifacts, dict)
                        and isinstance(current_artifact_id, str)
                    ):
                        artifact = artifacts.get(
                            current_artifact_id
                        )

                        if isinstance(artifact, dict):
                            final_artifact = artifact

                            yield emit(
                                RunEventType.ARTIFACT_CREATED,
                                {
                                    "artifact_id": current_artifact_id,
                                    "artifact": artifact,
                                },
                            )

            final_snapshot = await self._graph.aget_state(
                graph_config(thread_id),
            )
            interaction = _extract_interrupt_payload(
                final_snapshot,
            )

            if interaction is not None:
                tool_call = interaction_to_tool_call(
                    interaction,
                )

                requires_action = {
                    "status": "requires_action",
                    "required_action": {
                        "type": "submit_tool_outputs",
                        "tool_calls": [
                            tool_call.model_dump(mode="json")
                        ],
                    },
                }

                final_message = str(
                    interaction.get("question")
                    or "Уточните, пожалуйста, данные."
                )

                yield emit(
                    RunEventType.RUN_REQUIRES_ACTION,
                    requires_action,
                )

        except Exception as exc:
            safe = user_fallback_for_exception(exc)

            yield emit(
                RunEventType.RUN_FAILED,
                RunError(
                    code=safe.code.value,
                    message=safe.message,
                    retryable=safe.retryable,
                    retry_after_ms=safe.retry_after_ms,
                ).model_dump(mode="json"),
            )
            return

        if final_message:
            await self._memory.add_thread_message_for_user(
                user_id=user_id,
                thread_id=thread_id,
                role="assistant",
                content=final_message,
                artifact=final_artifact,
            )

        yield emit(
            RunEventType.RUN_COMPLETED,
            {
                "status": (
                    "requires_action"
                    if requires_action is not None
                    else "completed"
                ),
                "duration_ms": round(
                    (time.monotonic() - started_at) * 1000
                ),
                "content": final_message,
                "artifact": final_artifact,
                "required_action": (
                    requires_action["required_action"]
                    if requires_action is not None
                    else None
                ),
            },
        )

    async def _new_turn_input(
        self,
        *,
        snapshot: Any,
        user_id: str,
        thread_id: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Для нового thread собирает полный initial state.
        Для существующего checkpoint-а передаёт только новое user message.
        """
        values = getattr(snapshot, "values", None)

        if not isinstance(values, dict) or not values:
            return build_initial_state(
                user_id=user_id,
                thread_id=thread_id,
                first_message=HumanMessage(content=text),
            )

        stored_user_id = values.get("user_id")

        if stored_user_id and stored_user_id != user_id:
            raise PermissionError(
                "Checkpoint user does not match authenticated user"
            )

        return {
            "messages": [
                HumanMessage(content=text),
            ],
            "user_id": user_id,
        }

    def _resolve_interrupt_output(
        self,
        *,
        interrupt_payload: dict[str, Any],
        request: CreateRunRequest,
    ) -> Command:
        if not request.tool_outputs:
            raise ValueError(
                "Thread is awaiting one tool output"
            )

        submitted = request.tool_outputs[0]

        expected = interaction_to_tool_call(
            interrupt_payload,
        )

        if submitted.tool_call_id != expected.id:
            raise InvalidToolOutputError(
                "tool_call_id does not match current interaction"
            )

        return Command(resume=submitted.output)


def _extract_interrupt_payload(
    snapshot: Any,
) -> dict[str, Any] | None:
    """
    LangGraph snapshot.interrupts содержит Interrupt objects, у которых value
    является payload, переданным в interrupt(...).
    """
    interrupts = getattr(snapshot, "interrupts", ()) or ()

    for item in interrupts:
        value = getattr(item, "value", None)

        if isinstance(value, dict):
            return value

    return None


def _resume_to_text(
    value: Any,
) -> str:
    """
    Human-readable representation resume output для persisted chat history.
    """
    if isinstance(value, str):
        return value

    if isinstance(value, bool):
        return "Да" if value else "Нет"

    if isinstance(value, dict):
        values = [
            f"{key}: {item}"
            for key, item in value.items()
            if item not in (None, "", [], {}, "—")
        ]

        return "; ".join(values) or "(ответ отправлен)"

    return str(value)


def _unpack_stream_item(
    item: Any,
) -> tuple[tuple[Any, ...], str, Any]:
    """
    LangGraph versions могут вернуть:
      (namespace, mode, chunk)
    либо stream item иной формы. Нормализуем безопасно.
    """
    if (
        isinstance(item, tuple)
        and len(item) == 3
    ):
        namespace, mode, chunk = item

        return (
            tuple(namespace) if isinstance(namespace, tuple) else (),
            str(mode),
            chunk,
        )

    return (), "", item


def _normalize_progress(
    chunk: Any,
) -> dict[str, Any] | None:
    if not isinstance(chunk, dict):
        return None

    if chunk.get("visibility", "user") != "user":
        return None

    message = str(
        chunk.get("message")
        or "Выполняю задачу"
    )

    return {
        "message": message,
        "worker_id": chunk.get("worker_id"),
        "node": chunk.get("node"),
        "stage": chunk.get("stage")
        or chunk.get("node"),
    }


def _iter_state_updates(
    chunk: Any,
) -> list[dict[str, Any]]:
    """
    updates stream mode может содержать:
      {"node_name": {"field": "value"}}
    """
    if not isinstance(chunk, dict):
        return []

    updates: list[dict[str, Any]] = []

    for value in chunk.values():
        if isinstance(value, dict):
            updates.append(value)

    return updates