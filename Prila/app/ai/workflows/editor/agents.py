from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.editor.contracts import (
    EditIntentDecision,
    EditProposal,
)
from app.services.llm import llm_client


_INTENT_AGENT = "editor_intent"
_PROPOSAL_AGENT = "editor_proposal"

_INTENT_HISTORY_LIMIT = 20
_PROPOSAL_HISTORY_LIMIT = 20


_INTENT_PROMPT = """
Ты — Editor Intent Agent единого ассистента по RCA-справкам.

Тебе переданы:
- пользовательская команда редактирования;
- компактное содержание текущей RCA-справки;
- нумерованный список системных мер.

Определи одну операцию:

- action="edit_text": пользователь хочет изменить один текстовый раздел.
  Укажи section и instruction.

- action="edit_task": пользователь хочет добавить, заменить или удалить
  одну системную меру. Укажи task_operation, instruction и task_index:
  task_index является 0-based индексом в переданном списке tasks.
  Для add task_index не указывай.

- action="clarify": неясно, что именно редактировать, пользователь запросил
  несколько независимых правок сразу, или не указал нужную меру/раздел.
  Верни короткий понятный question.

Разрешённые text sections:
analysis, summary, root_cause, impact, timeline, applied_measures,
open_questions, limitations.

Правила:
- Не принимай новые факты об инциденте как обычную правку. Если пользователь
  сообщает лог, метрику, результат rollback, изменение конфигурации или
  другой evidence, который может менять RCA, задай clarify question:
  попроси подтвердить, что нужен повторный RCA с новым фактом.
- Не генерируй новое содержимое sections/tasks на этой стадии.
- Не выполняй изменение и не проси подтверждение на этой стадии.
- Не придумывай номер существующей меры.
- Верни только JSON строго по схеме EditIntentDecision.
"""


_PROPOSAL_PROMPT = """
Ты — Editor Proposal Agent RCA-справки.

Тебе переданы:
- точное решение Editor Intent Agent-а;
- текущий content только целевой секции или tasks;
- структурированный RCA context;
- инструкция пользователя.

Сформируй EditProposal — preview одного изменения.

Для text change:
- меняй только указанную section;
- сохраняй фактическую точность;
- не добавляй новых логов, метрик, дат, систем, evidence или выводов;
- если пользователь просит улучшить формулировку, сохраняй исходный смысл.

Для task change:
- add: создай одну конкретную ProposedTask;
- replace: замени только указанную меру, опираясь на RCA context;
- remove: верни original_task без new_task;
- не изменяй другие tasks;
- новая/заменённая мера должна устранять конкретную root cause или
  contributing factor и иметь проверяемый expected_result;
- не возвращай общие пожелания.

Preview не применяется автоматически. Не выполняй persistence.
Верни только JSON строго по схеме EditProposal.
"""


@dataclass(frozen=True, slots=True)
class EditorIntentOutcome:
    decision: EditIntentDecision
    snapshot_data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EditorProposalOutcome:
    proposal: EditProposal
    snapshot_data: dict[str, Any]


async def run_editor_intent(
    *,
    snapshot: TaskSnapshot,
    report_summary: dict[str, Any],
    tasks: list[dict[str, Any]],
    user_text: str,
) -> EditorIntentOutcome:
    """
    Определяет target edit из natural language.

    `tasks` передаём нумерованно как реальный state artifact, поэтому LLM
    выбирает только существующий task index, а workflow затем дополнительно
    проверяет границы массива.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_INTENT_AGENT,
        role="user",
        kind="edit_request",
        payload={"text": user_text},
        max_events=_INTENT_HISTORY_LIMIT,
    )

    indexed_tasks = [
        {
            "task_index": index,
            "user_number": index + 1,
            "task": task,
        }
        for index, task in enumerate(tasks)
    ]

    system = llm_client.build_system_message(
        role_instruction=_INTENT_PROMPT,
        extra_context={
            "report_summary": report_summary,
            "tasks": indexed_tasks,
            "agent_history": get_agent_events(
                data,
                agent=_INTENT_AGENT,
            ),
        },
        output_contract="JSON строго по схеме EditIntentDecision.",
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        EditIntentDecision,
        worker_kind="editor_intent",
    )

    data = append_agent_event(
        data,
        agent=_INTENT_AGENT,
        role="assistant",
        kind="intent_decision",
        payload=decision.model_dump(mode="json"),
        max_events=_INTENT_HISTORY_LIMIT,
    )

    return EditorIntentOutcome(
        decision=decision,
        snapshot_data=data,
    )


async def run_editor_proposal(
    *,
    snapshot: TaskSnapshot,
    intent: EditIntentDecision,
    target_content: Any,
    rca_context: dict[str, Any],
) -> EditorProposalOutcome:
    """
    Формирует preview без side effect.

    `target_content` строит workflow только из реального artifact state:
    агент не получает доступа к произвольным другим sections.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_PROPOSAL_AGENT,
        role="system",
        kind="proposal_input",
        payload={
            "intent": intent.model_dump(mode="json"),
            "target_content": target_content,
        },
        max_events=_PROPOSAL_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_PROPOSAL_PROMPT,
        extra_context={
            "intent": intent.model_dump(mode="json"),
            "target_content": target_content,
            "rca_context": rca_context,
            "agent_history": get_agent_events(
                data,
                agent=_PROPOSAL_AGENT,
            ),
        },
        output_contract="JSON строго по схеме EditProposal.",
    )

    proposal = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(
                content=(
                    "Сформируй preview изменения по переданной инструкции."
                )
            ),
        ],
        EditProposal,
        worker_kind="editor_proposal",
    )

    data = append_agent_event(
        data,
        agent=_PROPOSAL_AGENT,
        role="assistant",
        kind="edit_proposal",
        payload=proposal.model_dump(mode="json"),
        max_events=_PROPOSAL_HISTORY_LIMIT,
    )

    return EditorProposalOutcome(
        proposal=proposal,
        snapshot_data=data,
    )