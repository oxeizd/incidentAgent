from __future__ import annotations

from enum import Enum

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.conversation import ConversationTask
from app.ai.schemas.conversation_state import ConversationState
from app.services.llm import llm_client


GUARD_PROMPT = """
Ты управляешь жизненным циклом текущей задачи IT-ассистента.

По последнему сообщению пользователя определи, как поступить с current_task
и suspended_task. Верни только JSON GuardDecision.

Действия:

- answer_user_input: пользователь отвечает на вопрос, который задала
  current_task. Используй по умолчанию, если current_task ждёт пользователя.
- continue_current_task: пользователь просит продолжить текущую задачу без
  изменения цели.
- cancel_current_task: пользователь явно просит остановить текущую задачу
  целиком: «стоп», «отмени», «не надо», «прекрати».
- cancel_step: пользователь явно отменяет отдельную работу внутри текущей
  задачи, например «не делай RCA INC-123». Заполни target_step_id только ID
  существующего шага current_task.
- change_current_task: пользователь меняет цель или параметры текущей
  незавершённой задачи. Заполни updated_goal.
- resume_suspended_task: пользователь явно просит вернуться к отложенной
  задаче.
- clarify_resume_target: пользователь просит продолжить прошлую работу, но
  неясно, текущую или отложенную. Заполни clarification_question.
- start_new_task: пользователь явно переключился на новую несвязанную задачу.
  Router отложит current_task и отправит новое сообщение planner-у.
- answer_chat: приветствие, благодарность, общий вопрос или сообщение, которое
  не должно менять current_task. Заполни chat_response.

Правила:

1. Если current_task.status="waiting_for_user", новое сообщение по умолчанию
   является ответом на pending_user_input. Используй answer_user_input, если
   пользователь явно не отменяет или не меняет задачу.
2. Приветствие, благодарность и справочные вопросы не останавливают текущую
   задачу. Используй answer_chat.
3. cancel_current_task выбирай только при явной отмене всей задачи.
4. cancel_step выбирай только при явной отмене конкретного шага. Не выдумывай
   target_step_id: используй только ID из task context.
5. start_new_task выбирай только при явном смысловом переходе к другой работе.
6. updated_goal передавай только для change_current_task.
7. clarification_question передавай только для clarify_resume_target.
8. chat_response передавай только для answer_chat.
9. Не упоминай planner, executor, DAG, workflow, шаги, артефакты, state или
   внутренние реализации в reason, question и chat_response.
"""


class GuardAction(str, Enum):
    answer_user_input = "answer_user_input"
    continue_current_task = "continue_current_task"
    cancel_current_task = "cancel_current_task"
    cancel_step = "cancel_step"
    change_current_task = "change_current_task"
    resume_suspended_task = "resume_suspended_task"
    clarify_resume_target = "clarify_resume_target"
    start_new_task = "start_new_task"
    answer_chat = "answer_chat"


class GuardDecision(BaseModel):
    """Решение guard-а по отношению нового сообщения к текущей задаче."""

    model_config = ConfigDict(extra="forbid")

    action: GuardAction
    reason: str = Field(min_length=1, max_length=500)

    target_step_id: str | None = None
    updated_goal: str | None = None
    clarification_question: str | None = None
    chat_response: str | None = None


def _task_context(
    task: ConversationTask | None,
) -> dict[str, object] | None:
    if task is None:
        return None

    return {
        "task_id": task.task_id,
        "goal": task.goal,
        "status": task.status,
        "pending_user_input": (
            {
                "step_id": task.pending_user_input.step_id,
                "kind": task.pending_user_input.kind,
                "question": task.pending_user_input.question,
            }
            if task.pending_user_input is not None
            else None
        ),
        "steps": [
            {
                "step_id": step.step_id,
                "kind": step.kind,
                "goal": step.goal,
                "label": step.user_visible_label,
                "status": task.snapshot.step_runs[step.step_id].status,
            }
            for step in task.snapshot.plan.steps
        ],
    }


async def guard_message(
    state: ConversationState,
) -> GuardDecision | None:
    """
    Определяет отношение последнего сообщения к текущей/отложенной задаче.

    Когда задач нет, guard не нужен: router сразу вызывает planner.
    """
    if state["active_task"] is None and state["suspended_task"] is None:
        return None

    latest_message = state["messages"][-1]
    user_text = str(latest_message.content).strip()

    system_message = llm_client.build_system_message(
        role_instruction=GUARD_PROMPT,
        extra_context={
            "current_task": _task_context(state["active_task"]),
            "suspended_task": _task_context(state["suspended_task"]),
        },
        output_contract="JSON GuardDecision.",
    )

    return await llm_client.ainvoke_structured(
        [system_message, HumanMessage(content=user_text)],
        GuardDecision,
        worker_kind="guard",
    )