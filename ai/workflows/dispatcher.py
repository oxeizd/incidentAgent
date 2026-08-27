from __future__ import annotations

from typing import Any

from app.ai.workflows.executor import (
    ExecutionError,
    ExecutionRegistry,
    StepExecutionResult,
    StepWorker,
    execute_ready_steps,
)
from app.ai.schemas.conversation import PlanStep
from app.ai.schemas.conversation_state import ConversationState


_registry = ExecutionRegistry()


def register_step_worker(
    *,
    kind: str,
    worker: StepWorker,
) -> None:
    """
    Регистрирует domain worker для шага ExecutionPlan.

    Вызывается при startup из registry/bootstrap.py. Допустимые kind сейчас:
    search, rca, presentation.
    """
    _registry.register(kind, worker)


async def dispatch_start(
    state: ConversationState,
) -> dict[str, Any]:
    """Запускает все шаги нового плана, которые готовы на первом проходе."""
    return await execute_ready_steps(state, registry=_registry)


async def dispatch_continue(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Продолжает выполнение после нового scheduler tick.

    Используется, когда пользователь просит продолжить текущую задачу или
    когда background runtime возобновляет задачу после паузы.
    """
    return await execute_ready_steps(state, registry=_registry)


async def dispatch_user_input(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Продолжает DAG после receive_user_input().

    Ответ пользователя уже добавлен в локальную переписку ожидающего шага
    lifecycle-слоем. Worker увидит этот текст как обычное HumanMessage.
    """
    return await execute_ready_steps(state, registry=_registry)


async def dispatch_after_step_cancel(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Продолжает независимые ветки после отмены одного шага.

    Executor пропустит downstream отменённой ветки и запустит остальные
    pending шаги, чьи зависимости уже готовы.
    """
    return await execute_ready_steps(state, registry=_registry)


def build_worker_result(
    *,
    output_refs: list,
    state_update: dict[str, Any] | None = None,
) -> StepExecutionResult:
    """
    Короткий helper для search/RCA/presentation worker-ов.

    Пример:
        return build_worker_result(
            output_refs=[search_result_ref],
            state_update={"last_status": "Поиск завершён."},
        )
    """
    return StepExecutionResult(
        output_refs=output_refs,
        state_update=state_update or {},
    )


def ensure_step_kind(step: PlanStep, expected_kind: str) -> None:
    """Защищает worker от ошибочной регистрации или вызова не своего шага."""
    if step.kind != expected_kind:
        raise ExecutionError(
            f"Expected {expected_kind!r} step, got {step.kind!r}."
        )