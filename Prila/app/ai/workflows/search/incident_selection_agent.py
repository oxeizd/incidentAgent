from __future__ import annotations

from langchain_core.messages import HumanMessage

from app.ai.schemas.conversation import (
    ConversationTask,
    TaskSnapshot,
)
from app.ai.workflows.search.contracts import (
    IncidentSelectionAnswer,
    IncidentSelectionDecision,
    SearchIncidentCandidate,
)
from app.services.llm import llm_client


_SELECTION_PROMPT = """
Ты — агент выбора инцидента в процессе поиска для последующего RCA или
создания презентации.

Тебе переданы реальные preview-кандидаты, найденные Search workflow.
Каждый кандидат содержит:
- entity_id — внутренний ID варианта;
- number — номер инцидента;
- label — краткое описание.

Ты не ищешь новые инциденты, не вызываешь инструменты, не проводишь RCA и
не выбираешь инцидент молча.

Твоя задача:

1. Если среди preview-кандидатов можно показать пользователю варианты,
верни action="select_incident":
- задай короткий понятный question;
- в option_entity_ids включи только entity_id реальных кандидатов;
- если кандидат один, всё равно попроси пользователя подтвердить, что его
нужно использовать.

2. Если выдача не позволяет осмысленно предложить кандидатов (например,
нет доступных строк, нет номера инцидента, запрос слишком общий), верни
action="refine_search":
- задай вопрос, какие условия поиска сузить;
- option_entity_ids оставь пустым.

Не выбирай итоговый incident вместо пользователя.
Не придумывай номера, entity_id, описание или дополнительные результаты.

Верни JSON строго по схеме IncidentSelectionDecision.
"""


_ANSWER_PROMPT = """
Ты интерпретируешь обычный текстовый ответ пользователя на вопрос выбора
инцидента.

Тебе передан опубликованный список вариантов. Пользователь может ответить
номером пункта, номером инцидента, «да» при единственном варианте, фразой
«первый», названием или кратким описанием.

Верни:

- action="selected" и entity_id только из опубликованных вариантов, если
  пользовательский ответ можно надёжно сопоставить с одним вариантом;
- action="unclear" и короткий question, если сопоставить ответ нельзя.

Не придумывай entity_id, номер инцидента или новые варианты.
Верни JSON строго по схеме IncidentSelectionAnswer.
"""


class IncidentSelectionError(RuntimeError):
    """Контролируемая ошибка выбора incident candidate."""


async def decide_incident_selection(
    *,
    candidates: list[SearchIncidentCandidate],
    parent_kind: str,
) -> IncidentSelectionDecision:
    """
    Строит вопрос выбора либо вопрос уточнения search.

    LLM видит только preview-кандидаты, а runtime позднее сверит все
    option_entity_ids с этими данными.
    """
    system = llm_client.build_system_message(
        role_instruction=_SELECTION_PROMPT,
        extra_context={
            "parent_goal": (
                "RCA"
                if parent_kind == "rca"
                else "презентация"
            ),
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in candidates
            ],
        },
        output_contract=(
            "JSON строго по схеме IncidentSelectionDecision."
        ),
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(
                content=(
                    "Подготовь следующий шаг выбора инцидента."
                )
            ),
        ],
        IncidentSelectionDecision,
        worker_kind="search_selector",
    )

    _validate_option_ids(
        decision=decision,
        candidates=candidates,
    )

    return decision


async def interpret_incident_answer(
    *,
    question: str,
    candidates: list[SearchIncidentCandidate],
    user_text: str,
) -> IncidentSelectionAnswer:
    """
    Интерпретирует только текстовый ответ на уже опубликованный вопрос.

    Никаких Python regex/number parsing. Если LLM ошибочно вернёт чужой ID,
    runtime отклонит ответ и Search workflow повторит Interaction.
    """
    system = llm_client.build_system_message(
        role_instruction=_ANSWER_PROMPT,
        extra_context={
            "question": question,
            "options": [
                candidate.model_dump(mode="json")
                for candidate in candidates
            ],
        },
        output_contract=(
            "JSON строго по схеме IncidentSelectionAnswer."
        ),
    )

    answer = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        IncidentSelectionAnswer,
        worker_kind="search_selector",
    )

    known_ids = {
        candidate.entity_id
        for candidate in candidates
    }

    if answer.action == "selected":
        known_ids = {
            candidate.entity_id
            for candidate in candidates
        }

        if answer.entity_id not in known_ids:
            raise IncidentSelectionError(
                "Выбранный LLM вариант отсутствует среди options."
            )

    return answer


def _validate_option_ids(
    *,
    decision: IncidentSelectionDecision,
    candidates: list[SearchIncidentCandidate],
) -> None:
    available_ids = {
        candidate.entity_id
        for candidate in candidates
    }

    unknown_ids = set(decision.option_entity_ids) - available_ids

    if unknown_ids:
        raise IncidentSelectionError(
            "Selection decision references unknown preview candidates: "
            f"{sorted(unknown_ids)}"
        )