from __future__ import annotations

from typing import Any

from app.ai.runtime.task_lifecycle import switch_to_suspended
from app.ai.workflows.updates import merge_state_updates
from langchain_core.messages import AIMessage

from app.ai.agents.conversation_guard import (
    GuardAction,
    guard_message,
)
from app.ai.agents.conversation_planner import plan_message
from app.ai.runtime.task_lifecycle import (
    TaskLifecycleError,
    cancel_active,
    start_task,
    suspend_active,
)
from app.ai.schemas.conversation import ConversationPlan
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.chat import answer_chat
from app.ai.workflows.dispatcher import (
    dispatch_continue,
    dispatch_refine,
    dispatch_resume,
    dispatch_start,
)


async def handle_user_turn(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Верхнеуровневая обработка нового user message.

    Новый state уже должен содержать последнюю HumanMessage: transport
    добавляет её в LangGraph state до вызова этой функции.
    """
    try:
        decision = await guard_message(state)

        if decision is not None:
            update = await _handle_guard_decision(
                state,
                action=decision.action,
                goal_hint=decision.updated_goal_hint,
                clarification_question=decision.clarification_question,
            )

            if update is not None:
                return update

        return await _plan_and_dispatch(state)

    except TaskLifecycleError:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Не удалось корректно продолжить текущую задачу. "
                        "Попробуйте повторить запрос."
                    )
                )
            ],
            "last_error_message": (
                "Conversation task lifecycle invariant failed."
            ),
        }
    except Exception:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Не удалось обработать запрос. "
                        "Попробуйте ещё раз немного позже."
                    )
                )
            ],
            "last_error_message": (
                "Unhandled conversation router error."
            ),
        }


async def _handle_guard_decision(
    state: ConversationState,
    *,
    action: GuardAction,
    goal_hint: str | None,
    clarification_question: str | None,
) -> dict[str, Any] | None:
    """
    Возвращает update, когда действие уже обработано.

    Возвращает None только для new_request: active task при необходимости
    откладывается, после чего тот же user message передаётся Planner-у.
    """
    user_text = str(state["messages"][-1].content).strip()

    if action == GuardAction.answer_pending:
        return await dispatch_resume(
            state,
            user_text=user_text,
        )

    if action == GuardAction.continue_task:
        return await dispatch_continue(
            state,
            user_text=user_text,
        )

    if action == GuardAction.refine_task:
        return await dispatch_refine(
            state,
            user_text=user_text,
            goal_hint=goal_hint,
        )

    if action == GuardAction.cancel_task:
        update = cancel_active(state)

        return {
            **update,
            "messages": [
                AIMessage(
                    content="Хорошо, текущую задачу отменил."
                )
            ],
            "last_status": "Текущая задача отменена.",
        }

    if action == GuardAction.resume_suspended:
        if state["suspended_task"] is None:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Не нашёл отложенную задачу, "
                            "к которой можно вернуться."
                        )
                    )
                ]
            }

        switched = switch_to_suspended(
            state,
            reason=(
                "Пользователь переключился "
                "на ранее отложенную задачу."
            ),
        )

        resumed_state = {
            **state,
            **switched,
        }

        return await dispatch_continue(
            resumed_state,
            user_text=user_text,
        )

    if action == GuardAction.clarify_resume_target:
        question = (
            clarification_question
            or "Что именно вы хотите продолжить?"
        )

        return {
            "messages": [
                AIMessage(content=question)
            ],
            "last_status": "Ожидаю выбор задачи для продолжения.",
        }

    if action == GuardAction.chat:
        message = await answer_chat(user_text=user_text)

        return {
            "messages": [message],
        }

    if action == GuardAction.new_request:
        if state["active_task"] is None:
            return None

        suspended = suspend_active(
            state,
            reason="Пользователь начал новую задачу.",
        )

        suspended_state = {
            **state,
            **suspended,
        }

        return await _plan_and_dispatch(suspended_state)

    raise ValueError(
        f"Unsupported guard action: {action!r}"
    )


async def _plan_and_dispatch(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Planner → chat response либо start active workflow.

    Поиск как dependency не становится search task: создаём конечный task
    (`rca`/`presentation`), а workflow начинает со search-normalization
    stage, используя ConversationPlan в snapshot.data.
    """
    plan = await plan_message(state)

    if plan.intent == "chat":
        return {
            "messages": [
                AIMessage(content=plan.chat_response or "")
            ]
        }

    update = _start_task_from_plan(
        state,
        plan=plan,
    )

    started_state = {
        **state,
        **update,
    }

    workflow_update = await dispatch_start(started_state)

    return merge_state_updates(
        update,
        workflow_update,
        {
            "turn_count": state["turn_count"] + 1,
        },
    )


def _start_task_from_plan(
    state: ConversationState,
    *,
    plan: ConversationPlan,
) -> dict[str, Any]:
    """
    Создаёт task и сохраняет исходный plan в snapshot.

    `initial_data["plan"]` — единственный канал передачи планировочного
    решения workflow-у. Workflow не читает raw planner fields из root state.
    """
    if plan.intent == "chat":
        raise ValueError(
            "Chat plan must not create ConversationTask"
        )

    refs = (
        [plan.target_ref]
        if plan.target_ref is not None
        else []
    )

    return start_task(
        state,
        kind=plan.intent,
        goal=plan.goal,
        initial_stage="start",
        initial_data={
            "plan": plan.model_dump(mode="json"),
        },
        refs=refs,
    )