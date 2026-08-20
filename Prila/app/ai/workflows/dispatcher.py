from __future__ import annotations

from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.registry import get_workflow


async def dispatch_start(
    state: ConversationState,
) -> dict:
    """
    Запускает workflow только после создания active_task.

    Router вызывает это после start_task(), поэтому workflow всегда видит
    валидную active_task и не обязан повторять lifecycle-проверки.
    """
    task = state["active_task"]

    if task is None:
        raise ValueError(
            "Cannot dispatch workflow start without active_task"
        )

    workflow = get_workflow(task.kind)

    return await workflow.start(
        state,
        task,
    )


async def dispatch_resume(
    state: ConversationState,
    *,
    user_text: str,
) -> dict:
    """
    Передаёт обычный текстовый ответ workflow-владельцу Interaction.
    """
    task = state["active_task"]

    if task is None:
        raise ValueError(
            "Cannot dispatch resume without active_task"
        )

    workflow = get_workflow(task.kind)

    return await workflow.resume(
        state,
        task,
        user_text=user_text,
    )


async def dispatch_continue(
    state: ConversationState,
    *,
    user_text: str,
) -> dict:
    task = state["active_task"]

    if task is None:
        raise ValueError(
            "Cannot dispatch continue without active_task"
        )

    workflow = get_workflow(task.kind)

    return await workflow.continue_task(
        state,
        task,
        user_text=user_text,
    )


async def dispatch_refine(
    state: ConversationState,
    *,
    user_text: str,
    goal_hint: str | None,
) -> dict:
    task = state["active_task"]

    if task is None:
        raise ValueError(
            "Cannot dispatch refine without active_task"
        )

    workflow = get_workflow(task.kind)

    return await workflow.refine(
        state,
        task,
        user_text=user_text,
        goal_hint=goal_hint,
    )