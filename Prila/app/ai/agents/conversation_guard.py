from __future__ import annotations

from enum import Enum

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.conversation import ConversationTask
from app.ai.schemas.conversation_state import ConversationState
from app.services.llm import llm_client


_GUARD_FALLBACK_PROMPT = """
Ты определяешь отношение последнего сообщения пользователя к состоянию
единого ассистента по инцидентам, RCA, поручениям и презентациям.

У тебя может быть:
- активная задача;
- отложенная задача;
- ожидающий вопрос активной задачи;
- последнее сообщение пользователя.

Выбери одно действие:

answer_pending:
- сообщение является ответом на ожидающий вопрос активной задачи;
- при сомнении между ответом и новым запросом выбирай answer_pending.

continue_task:
- пользователь продолжает активную задачу, но сообщение не является прямым
  ответом на pending question;
- например уточняет цель, просит показать результат или меняет детали.

refine_task:
- пользователь меняет параметры активной задачи, сохраняя ту же цель;
- например другой период поиска, другая система, уточнение формата
  презентации, дополнительный факт для текущего RCA.

cancel_task:
- пользователь явно просит остановить или отменить активную задачу.

resume_suspended:
- пользователь явно просит вернуться к единственной отложенной задаче.

new_request:
- пользователь явно начал новую независимую задачу:
  поиск, RCA, редактирование, презентацию или обычный диалог вне текущей
  активной задачи.

chat:
- пользователь пишет приветствие, благодарность, справочный вопрос или
  другой обычный диалог, который не должен менять активную задачу.

Правила:
- Не выбирай new_request только потому, что новое сообщение короткое.
- При наличии pending question краткий ответ, число, вариант, уточнение
  или фраза по теме вопроса — обычно answer_pending.
- Приветствие, благодарность или вопрос о возможностях не должны отменять,
  приостанавливать или ломать текущую задачу: выбирай chat.
- cancel_task выбирай только при ясном намерении отменить именно активную
  задачу.
- resume_suspended выбирай только при ясном намерении вернуться к
  отложенной задаче.
- Если есть активная и отложенная задача, а пользователь просит «вернуться»
  без указания, выбери clarify_resume_target.
"""


class GuardAction(str, Enum):
    answer_pending = "answer_pending"
    continue_task = "continue_task"
    refine_task = "refine_task"
    cancel_task = "cancel_task"
    resume_suspended = "resume_suspended"
    clarify_resume_target = "clarify_resume_target"
    new_request = "new_request"
    chat = "chat"


class GuardDecision(BaseModel):
    """
    Минимальное решение Guard.

    Guard не планирует новый запрос и не модифицирует workflow state. Он
    только классифицирует связь реплики с lifecycle задач.
    """

    model_config = ConfigDict(extra="forbid")

    action: GuardAction
    reason: str = Field(min_length=1, max_length=500)

    updated_goal_hint: str | None = None
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "GuardDecision":
        if self.action == GuardAction.clarify_resume_target:
            if not self.clarification_question:
                raise ValueError(
                    "clarify_resume_target requires clarification_question"
                )

        if (
            self.action != GuardAction.clarify_resume_target
            and self.clarification_question is not None
        ):
            raise ValueError(
                "clarification_question allowed only for "
                "clarify_resume_target"
            )

        return self


def _task_context(
    task: ConversationTask | None,
) -> dict | None:
    """
    Сжимает task до LLM-safe контекста.

    Не отдаём модели целиком artifacts, worker snapshots, user_id или
    thread_id. Для Guard нужны только цель, этап, refs и ожидаемый вопрос.
    """
    if task is None:
        return None

    interaction = task.pending_interaction

    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "goal": task.goal,
        "status": task.status,
        "stage": task.snapshot.stage,
        "refs": [
            {
                "kind": ref.kind,
                "id": ref.id,
                "label": ref.label,
            }
            for ref in task.refs
        ],
        "pending_interaction": (
            {
                "kind": interaction.kind,
                "question": interaction.question,
                "options": [
                    {
                        "value": option.value,
                        "label": option.label,
                    }
                    for option in interaction.options
                ],
                "fields": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "required": field.required,
                    }
                    for field in interaction.fields
                ],
                "continuation_stage": (
                    interaction.continuation_stage
                ),
            }
            if interaction is not None
            else None
        ),
    }


async def guard_message(
    state: ConversationState,
) -> GuardDecision | None:
    """
    Возвращает Guard decision, только когда есть task context.

    Если нет active_task и suspended_task, planner сразу обрабатывает
    сообщение как потенциально новый запрос или chat.
    """
    active_task = state["active_task"]
    suspended_task = state["suspended_task"]

    if active_task is None and suspended_task is None:
        return None

    latest_message = state["messages"][-1]
    user_text = str(latest_message.content).strip()

    system = llm_client.build_system_message(
        role_instruction=_GUARD_FALLBACK_PROMPT,
        extra_context={
            "active_task": _task_context(active_task),
            "suspended_task": _task_context(suspended_task),
        },
        output_contract="JSON строго по схеме GuardDecision.",
    )

    return await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        GuardDecision,
        worker_kind="guard",
    )