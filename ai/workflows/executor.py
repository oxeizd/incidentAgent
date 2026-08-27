from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.ai.runtime.task_lifecycle import (
    TaskLifecycleError,
    append_step_message,
    finalize_active_task,
    get_active_task,
    get_plan_step,
    get_step_run,
    mark_step_completed,
    mark_step_failed,
    mark_step_running,
    ready_step_ids,
    skip_blocked_steps,
    wait_for_user_input,
)
from app.ai.schemas.conversation import (
    ArtifactRef,
    PlanStep,
    UserInputRequest,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.updates import merge_state_updates


@dataclass(frozen=True)
class StepExecutionResult:
    """
    Изолированный результат одного domain worker-а.

    Worker не меняет ConversationTask и не возвращает full state. Это сохраняет
    корректность при будущих параллельных ветках: executor применяет результаты
    одной волны последовательно к свежему state.

    `state_update` разрешён только для root-полей вне lifecycle: messages,
    current_report_ref, current_presentation_ref, last_status и т.п.
    """

    output_refs: list[ArtifactRef]
    assistant_message: str | None = None
    user_input_request: dict[str, Any] | None = None
    state_update: dict[str, Any] | None = None


StepWorker = Callable[
    [ConversationState, PlanStep],
    Awaitable[StepExecutionResult],
]


class ExecutionError(Exception):
    """Ошибка конфигурации или выполнения ExecutionPlan."""


class ExecutionRegistry:
    """Реестр domain worker-ов для search, rca и presentation шагов."""

    def __init__(self) -> None:
        self._workers: dict[str, StepWorker] = {}

    def register(self, kind: str, worker: StepWorker) -> None:
        if kind in self._workers:
            raise ExecutionError(f"Worker already registered: {kind!r}.")

        self._workers[kind] = worker

    def get(self, kind: str) -> StepWorker:
        worker = self._workers.get(kind)
        if worker is None:
            raise ExecutionError(f"No worker registered for: {kind!r}.")

        return worker


async def execute_ready_steps(
    state: ConversationState,
    *,
    registry: ExecutionRegistry,
) -> dict[str, Any]:
    """
    Исполняет все готовые шаги DAG.

    Перед первым запуском шаг получает `step.goal` как первое user-сообщение
    собственной локальной переписки. После user clarification следующий запуск
    использует уже накопленные реальные user/assistant сообщения.
    """
    current_state = state
    combined_update: dict[str, Any] = {}

    while True:
        task = get_active_task(current_state)
        if task.status != "running":
            break

        ready_ids = ready_step_ids(task)
        if not ready_ids:
            blocked_update = skip_blocked_steps(current_state)
            if blocked_update:
                current_state = {**current_state, **blocked_update}
                combined_update = merge_state_updates(
                    combined_update,
                    blocked_update,
                )
                continue

            if _is_finalizable(task):
                final_update = finalize_active_task(current_state)
                combined_update = merge_state_updates(
                    combined_update,
                    final_update,
                )

            break

        current_state, start_updates = _prepare_and_start_wave(
            current_state,
            step_ids=ready_ids,
        )
        combined_update = merge_state_updates(
            combined_update,
            *start_updates,
        )

        wave_snapshot = current_state
        results = await asyncio.gather(
            *[
                _execute_step(
                    wave_snapshot,
                    step_id=step_id,
                    registry=registry,
                )
                for step_id in ready_ids
            ],
            return_exceptions=True,
        )

        for step_id, result in zip(ready_ids, results, strict=True):
            current_state, result_update = _apply_step_result(
                current_state,
                step_id=step_id,
                result=result,
            )
            combined_update = merge_state_updates(
                combined_update,
                result_update,
            )

            if get_active_task(current_state).status != "running":
                break

        if get_active_task(current_state).status != "running":
            break


def _prepare_and_start_wave(
    state: ConversationState,
    *,
    step_ids: list[str],
) -> tuple[ConversationState, list[dict[str, Any]]]:
    current_state = state
    updates: list[dict[str, Any]] = []

    for step_id in step_ids:
        task = get_active_task(current_state)
        step = get_plan_step(task, step_id=step_id)
        step_run = get_step_run(task, step_id=step_id)

        if not step_run.conversation.messages:
            history_update = append_step_message(
                current_state,
                step_id=step_id,
                role="user",
                content=step.goal,
            )
            current_state = {**current_state, **history_update}
            updates.append(history_update)

        running_update = mark_step_running(current_state, step_id=step_id)
        current_state = {**current_state, **running_update}
        updates.append(running_update)

    return current_state, updates


async def _execute_step(
    state: ConversationState,
    *,
    step_id: str,
    registry: ExecutionRegistry,
) -> StepExecutionResult:
    task = get_active_task(state)
    step = get_plan_step(task, step_id=step_id)
    worker = registry.get(step.kind)

    result = await worker(state, step)
    if not isinstance(result, StepExecutionResult):
        raise ExecutionError(
            f"Worker {step.kind!r} returned invalid result for "
            f"step {step_id!r}."
        )

    _validate_worker_state_update(result.state_update)
    return result


def _apply_step_result(
    state: ConversationState,
    *,
    step_id: str,
    result: StepExecutionResult | BaseException,
) -> tuple[ConversationState, dict[str, Any]]:
    """Последовательно применяет результат одного worker-а к свежему state."""
    if isinstance(result, BaseException):
        update = mark_step_failed(
            state,
            step_id=step_id,
            error=_error_text(result),
        )
        return {**state, **update}, update

    current_state = state
    combined_update: dict[str, Any] = {}

    if result.state_update:
        current_state = {**current_state, **result.state_update}
        combined_update = merge_state_updates(
            combined_update,
            result.state_update,
        )

    if result.assistant_message:
        message_update = append_step_message(
            current_state,
            step_id=step_id,
            role="assistant",
            content=result.assistant_message,
        )
        current_state = {**current_state, **message_update}
        combined_update = merge_state_updates(
            combined_update,
            message_update,
        )

    if result.user_input_request is not None:
        request = UserInputRequest.model_validate(
            {
                **result.user_input_request,
                "step_id": step_id,
            }
        )
        waiting_update = wait_for_user_input(
            current_state,
            request=request,
        )
        current_state = {**current_state, **waiting_update}
        combined_update = merge_state_updates(
            combined_update,
            waiting_update,
        )
        return current_state, combined_update

    step_run = get_step_run(
        get_active_task(current_state),
        step_id=step_id,
    )
    if step_run.status in {"cancelled", "failed"}:
        return current_state, combined_update

    complete_update = mark_step_completed(
        current_state,
        step_id=step_id,
        output_refs=result.output_refs,
    )
    current_state = {**current_state, **complete_update}
    combined_update = merge_state_updates(
        combined_update,
        complete_update,
    )

    return current_state, combined_update


def _validate_worker_state_update(
    state_update: dict[str, Any] | None,
) -> None:
    if not state_update:
        return

    forbidden = {"active_task", "suspended_task"}.intersection(state_update)
    if forbidden:
        raise ExecutionError(
            "Worker state_update cannot modify task lifecycle: "
            f"{sorted(forbidden)}."
        )


def _is_finalizable(task: Any) -> bool:
    terminal_statuses = {
        "completed",
        "cancelled",
        "failed",
        "skipped",
    }
    return all(
        step_run.status in terminal_statuses
        for step_run in task.snapshot.step_runs.values()
    )


def _error_text(error: BaseException) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__