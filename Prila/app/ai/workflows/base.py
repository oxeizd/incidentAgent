from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from app.ai.schemas.conversation import ConversationTask
from app.ai.schemas.conversation_state import ConversationState


class WorkflowRuntime(Protocol):
    """
    Минимальный интерфейс workflow для root router.

    Каждый workflow сам хранит свои stage/data в task.snapshot, сам создаёт
    Interaction и сам возвращает update ConversationState.
    """

    async def start(
        self,
        state: ConversationState,
        task: ConversationTask,
    ) -> dict:
        ...

    async def resume(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        ...

    async def continue_task(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        ...

    async def refine(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
        goal_hint: str | None,
    ) -> dict:
        ...


WorkflowFactory = Callable[[], WorkflowRuntime]
WorkflowRegistry = dict[str, WorkflowFactory]