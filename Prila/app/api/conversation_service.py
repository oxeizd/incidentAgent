from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.schemas.conversation_state import (
    build_initial_conversation_state,
)
from app.api.events import (
    error_event,
    new_stream_id,
    result_event,
    status_event,
    unix_now,
)
from app.api.schemas import ConversationTurnRequest
from app.memory.facade import MemoryFacade


class ConversationRunConflictError(Exception):
    """Only one graph run is allowed for the same thread."""


_MAX_TRACKED_LOCKS = 4_096


def conversation_graph_config(
    thread_id: str,
) -> dict[str, Any]:
    """
    Единственный config builder для graph persistence.

    Один thread_id используется при каждом aget_state/astream/ainvoke
    данного conversation. Именно по нему LangGraph checkpointer загружает
    и сохраняет ConversationState.
    """
    return {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": 50,
    }


class ConversationService:
    """
    Application service одного user turn.

    Новый graph не использует Command(resume) и native interrupt:
    каждый запрос передаётся как HumanMessage, а Guard/Router решает,
    как обработать реплику с учётом active_task/pending_interaction.
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
        self._locks: OrderedDict[str, asyncio.Lock] = (
            OrderedDict()
        )

    def validate_turn(
        self,
        *,
        thread_id: str,
    ) -> None:
        if thread_id in self._in_flight:
            raise ConversationRunConflictError(
                "Thread is already processing a turn"
            )

    async def run_turn(
        self,
        *,
        thread_id: str,
        user_id: str,
        request: ConversationTurnRequest,
    ) -> AsyncIterator[dict[str, Any]]:
        if thread_id in self._in_flight:
            raise ConversationRunConflictError(
                "Thread is already processing a turn"
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

    def _lock_for(
        self,
        thread_id: str,
    ) -> asyncio.Lock:
        lock = self._locks.get(thread_id)

        if lock is not None:
            self._locks.move_to_end(thread_id)
            return lock

        lock = asyncio.Lock()
        self._locks[thread_id] = lock

        while len(self._locks) > _MAX_TRACKED_LOCKS:
            self._locks.popitem(last=False)

        return lock

    async def _run_locked(
        self,
        *,
        thread_id: str,
        user_id: str,
        request: ConversationTurnRequest,
    ) -> AsyncIterator[dict[str, Any]]:
        stream_id = new_stream_id()
        created = unix_now()
        config = conversation_graph_config(thread_id)

        try:
            snapshot = await self._graph.aget_state(config)
            graph_input = self._build_turn_input(
                snapshot=snapshot,
                thread_id=thread_id,
                user_id=user_id,
                text=request.text,
            )
        except PermissionError:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Диалог недоступен для текущего пользователя."
                ),
                retryable=False,
            )
            return
        except Exception:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Не удалось загрузить состояние диалога. "
                    "Попробуйте ещё раз."
                ),
                retryable=True,
            )
            return

        try:
            await self._memory.add_thread_message_for_user(
                user_id=user_id,
                thread_id=thread_id,
                role="user",
                content=request.text,
            )
        except Exception:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Не удалось сохранить сообщение пользователя."
                ),
                retryable=True,
            )
            return

        yield status_event(
            stream_id=stream_id,
            created=created,
            status="Обрабатываю запрос.",
        )

        try:
            await self._graph.ainvoke(
                graph_input,
                config=config,
            )

            final_snapshot = await self._graph.aget_state(
                config,
            )
        except Exception:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Не удалось завершить обработку запроса. "
                    "Попробуйте ещё раз."
                ),
                retryable=True,
            )
            return

        state = getattr(final_snapshot, "values", None)

        if not isinstance(state, dict):
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Диалог завершился без корректного состояния. "
                    "Попробуйте ещё раз."
                ),
                retryable=True,
            )
            return

        message = _last_assistant_message(state)

        if not message:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Задача завершилась без ответа. "
                    "Попробуйте ещё раз."
                ),
                retryable=True,
            )
            return

        active_task = state.get("active_task")
        suspended_task = state.get("suspended_task")

        public_active_task = _serialize_task(active_task)
        public_suspended_task = _serialize_task(
            suspended_task,
        )
        pending_interaction = _serialize_interaction(
            active_task,
        )
        artifact = _current_artifact(state)

        try:
            await self._memory.add_thread_message_for_user(
                user_id=user_id,
                thread_id=thread_id,
                role="assistant",
                content=message,
                artifact=artifact,
            )
        except Exception:
            yield error_event(
                stream_id=stream_id,
                created=created,
                message=(
                    "Ответ сформирован, но не удалось сохранить "
                    "его в истории диалога."
                ),
                retryable=True,
            )
            return

        yield result_event(
            stream_id=stream_id,
            created=created,
            message=message,
            task=public_active_task,
            suspended_task=public_suspended_task,
            pending_interaction=pending_interaction,
            artifact=artifact,
        )

    def _build_turn_input(
        self,
        *,
        snapshot: Any,
        thread_id: str,
        user_id: str,
        text: str,
    ) -> dict[str, Any]:
        values = getattr(snapshot, "values", None)

        if not isinstance(values, dict) or not values:
            return build_initial_conversation_state(
                user_id=user_id,
                thread_id=thread_id,
                first_message=HumanMessage(content=text),
                session_start_at=_utc_now_iso(),
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
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _last_assistant_message(
    state: dict[str, Any],
) -> str | None:
    messages = state.get("messages")

    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        content = getattr(message, "content", None)

        if isinstance(content, str) and content.strip():
            return content.strip()

    return None


def _serialize_task(
    raw_task: Any,
) -> dict[str, Any] | None:
    if raw_task is None:
        return None

    if hasattr(raw_task, "model_dump"):
        task = raw_task.model_dump(mode="json")
    elif isinstance(raw_task, dict):
        task = raw_task
    else:
        return None

    snapshot = task.get("snapshot")

    if not isinstance(snapshot, dict):
        return None

    refs = task.get("refs")

    return {
        "task_id": task.get("task_id"),
        "kind": task.get("kind"),
        "goal": task.get("goal"),
        "status": task.get("status"),
        "stage": snapshot.get("stage"),
        "refs": refs if isinstance(refs, list) else [],
        "suspended_at": task.get("suspended_at"),
        "suspension_reason": task.get(
            "suspension_reason"
        ),
    }


def _serialize_interaction(
    raw_task: Any,
) -> dict[str, Any] | None:
    if raw_task is None:
        return None

    interaction = getattr(
        raw_task,
        "pending_interaction",
        None,
    )

    if interaction is None and isinstance(raw_task, dict):
        interaction = raw_task.get("pending_interaction")

    if interaction is None:
        return None

    if hasattr(interaction, "model_dump"):
        return interaction.model_dump(mode="json")

    return interaction if isinstance(interaction, dict) else None


def _current_artifact(
    state: dict[str, Any],
) -> dict[str, Any] | None:
    artifacts = state.get("artifacts")
    artifact_id = state.get("current_artifact_id")

    if (
        not isinstance(artifacts, dict)
        or not isinstance(artifact_id, str)
    ):
        return None

    artifact = artifacts.get(artifact_id)

    return artifact if isinstance(artifact, dict) else None