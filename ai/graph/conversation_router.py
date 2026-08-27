from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage

from app.ai.agents.conversation_guard import (
    GuardAction,
    GuardDecision,
    guard_message,
)
from app.ai.agents.planner import plan_message
from app.ai.runtime.task_lifecycle import (
    TaskLifecycleError,
    cancel_active_task,
    cancel_step_and_downstream,
    receive_user_input,
    resume_suspended_task,
    start_task,
    suspend_active_task,
)
from app.ai.schemas.conversation import PlannerDecision
from app.ai.schemas.conversation_state import (
    ConversationState,
    coerce_conversation_state,
)
from app.ai.workflows.dispatcher import (
    dispatch_after_step_cancel,
    dispatch_continue,
    dispatch_start,
    dispatch_user_input,
)
from app.ai.workflows.updates import merge_state_updates


logger = logging.getLogger(__name__)

CHAT_FALLBACK_TEXT = "Готов помочь. Что нужно сделать?"


async def handle_user_turn(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Единая точка входа для нового сообщения пользователя.

    Порядок обработки:
    1. Восстановить Pydantic-модели после checkpoint-а.
    2. Guard определяет отношение сообщения к текущей работе.
    3. Выполнить lifecycle-действие или отправить новый запрос planner-у.
    4. Dispatcher запускает готовые шаги ExecutionPlan.
    """
    state = coerce_conversation_state(state)

    try:
        decision = await guard_message(state)
        if decision is not None:
            update = await _handle_guard_decision(
                state=state,
                decision=decision,
            )
            if update is not None:
                return update

        return await _plan_and_dispatch(state)
    except TaskLifecycleError:
        logger.exception("Conversation task lifecycle error")
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Не удалось безопасно продолжить текущую работу. "
                        "Повторите запрос, пожалуйста."
                    )
                )
            ],
            "last_error_message": "Task lifecycle error.",
        }
    except Exception:
        logger.exception("Unhandled conversation router error")
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Не удалось обработать сообщение. "
                        "Попробуйте ещё раз."
                    )
                )
            ],
            "last_error_message": "Unhandled conversation router error.",
        }


async def _handle_guard_decision(
    *,
    state: ConversationState,
    decision: GuardDecision,
) -> dict[str, Any] | None:
    """Выполняет действие, выбранное guard-ом для текущего сообщения."""
    user_text = str(state["messages"][-1].content).strip()

    if decision.action == GuardAction.answer_user_input:
        lifecycle_update = receive_user_input(
            state,
            user_text=user_text,
        )
        resumed_state = {**state, **lifecycle_update}

        return merge_state_updates(
            lifecycle_update,
            await dispatch_user_input(resumed_state),
        )

    if decision.action == GuardAction.continue_current_task:
        return await dispatch_continue(state)

    if decision.action == GuardAction.cancel_current_task:
        update = cancel_active_task(state)
        return {
            **update,
            "messages": [
                AIMessage(content="Хорошо, текущую задачу отменяю."),
            ],
            "last_status": "Текущая задача отменена.",
        }

    if decision.action == GuardAction.cancel_step:
        if not decision.target_step_id:
            raise TaskLifecycleError(
                "cancel_step requires target_step_id."
            )

        lifecycle_update = cancel_step_and_downstream(
            state,
            step_id=decision.target_step_id,
        )
        updated_state = {**state, **lifecycle_update}

        return merge_state_updates(
            lifecycle_update,
            await dispatch_after_step_cancel(updated_state),
            {
                "messages": [
                    AIMessage(content="Хорошо, эту часть работы отменяю."),
                ],
                "last_status": "Часть текущей задачи отменена.",
            },
        )

    if decision.action == GuardAction.change_current_task:
        return await _replace_current_task(
            state=state,
            updated_goal=decision.updated_goal or user_text,
        )

    if decision.action == GuardAction.resume_suspended_task:
        lifecycle_update = resume_suspended_task(
            state,
            reason="Пользователь вернулся к отложенной задаче.",
        )
        resumed_state = {**state, **lifecycle_update}

        return merge_state_updates(
            lifecycle_update,
            await dispatch_continue(resumed_state),
        )

    if decision.action == GuardAction.clarify_resume_target:
        return {
            "messages": [
                AIMessage(
                    content=(
                        decision.clarification_question
                        or "Какую из незавершённых задач продолжить?"
                    )
                )
            ],
            "last_status": "Уточняю, какую задачу продолжить.",
        }

    if decision.action == GuardAction.start_new_task:
        return await _suspend_and_plan_new_task(state)

    if decision.action == GuardAction.answer_chat:
        return {
            "messages": [
                AIMessage(
                    content=decision.chat_response or CHAT_FALLBACK_TEXT
                )
            ],
        }

    raise TaskLifecycleError(
        f"Unsupported guard action: {decision.action!r}."
    )


async def _replace_current_task(
    *,
    state: ConversationState,
    updated_goal: str,
) -> dict[str, Any]:
    """
    Заменяет незавершённую текущую задачу новым планом.

    Старый план не пытаемся мутировать: это сохраняет ExecutionPlan
    неизменяемым. Его текущий прогресс остаётся одной отложенной задачей.
    """
    suspended_update = suspend_active_task(
        state,
        reason="Пользователь изменил цель текущей задачи.",
    )
    suspended_state = {**state, **suspended_update}

    return merge_state_updates(
        suspended_update,
        await _plan_and_dispatch(
            suspended_state,
            planner_user_text=updated_goal,
        ),
    )


async def _suspend_and_plan_new_task(
    state: ConversationState,
) -> dict[str, Any]:
    suspended_update = suspend_active_task(
        state,
        reason="Пользователь переключился на новую задачу.",
    )
    suspended_state = {**state, **suspended_update}

    return merge_state_updates(
        suspended_update,
        await _plan_and_dispatch(suspended_state),
    )


async def _plan_and_dispatch(
    state: ConversationState,
    *,
    planner_user_text: str | None = None,
) -> dict[str, Any]:
    """Получает PlannerDecision и либо отвечает в чат, либо запускает DAG."""
    current_time = datetime.now(timezone.utc).isoformat()

    planner_state = state
    if planner_user_text is not None:
        planner_state = {
            **state,
            "messages": [
                *state["messages"][:-1],
                state["messages"][-1].model_copy(
                    update={"content": planner_user_text}
                ),
            ],
        }

    decision = await plan_message(
        planner_state,
        current_time=current_time,
    )

    if decision.kind == "chat":
        return {
            "messages": [
                AIMessage(
                    content=decision.chat_response or CHAT_FALLBACK_TEXT
                )
            ],
            "turn_count": state["turn_count"] + 1,
        }

    plan = _require_execution_plan(decision)
    task_update = start_task(state, plan=plan)
    started_state = {**state, **task_update}

    logger.info(
        "Starting execution plan: task_id=%s plan_id=%s",
        task_update["active_task"].task_id,
        plan.plan_id,
    )

    return merge_state_updates(
        task_update,
        await dispatch_start(started_state),
        {"turn_count": state["turn_count"] + 1},
    )


def _require_execution_plan(decision: PlannerDecision):
    if decision.plan is None:
        raise TaskLifecycleError(
            "Planner returned execute decision without plan."
        )

    return decision.plan