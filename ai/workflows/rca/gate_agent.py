from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage

from app.ai.runtime.agent_messages import to_llm_messages
from app.ai.schemas.conversation import AgentConversation
from app.ai.workflows.rca.contracts import (
    RCAInput,
    RCAInvestigation,
    RCAQualityDecision,
    RCAReadinessDecision,
    RCAReportDraft,
    RCAValidationResult,
)
from app.services.llm import llm_client


GATE_PROMPT = """
Ты — обязательный RCA Gate для IT-инцидентов.

Ты не проводишь расследование вместо RCA Agent и не придумываешь evidence.
Ты принимаешь два режима проверки и всегда возвращаешь только JSON требуемого
контракта.

Режим readiness:
- проверь, достаточно ли исходного контекста, результатов исследования и
  ответов пользователя, чтобы продолжать RCA;
- action="proceed": можно строить или обновлять RCA draft;
- action="ask_user": отсутствуют критичные сведения; задай от 1 до 3
  конкретных вопросов;
- action="stop": RCA нельзя безопасно продолжать.

Режим quality:
- проверь готовый RCA draft перед сохранением;
- action="approve": draft можно сохранить;
- action="revise": draft нужно переработать; required_changes содержит
  конкретные правки;
- action="ask_user": нужны сведения пользователя; questions содержит от 1 до
  3 конкретных вопросов.

Правила достоверности:
- Не выдавай гипотезу за факт.
- Root cause может иметь kind="fact" только при прямых evidence.
- Если причина вероятна, но не доказана, это kind="hypothesis".
- Если причина неизвестна, draft обязан явно описывать limitations и
  open_questions; это допустимое RCA, если не заявлять ложную определённость.
- Симптом, триггер, workaround и root cause — разные сущности.
- Corrective/preventive actions должны быть связаны с причиной или
  contributing factor и иметь проверяемый ожидаемый результат.
- Не придумывай логи, даты, изменения, факты или доказательства.
- Не упоминай инструменты, workflow, prompts, агентов или внутреннюю
  реализацию в reason/questions/required_changes.
"""


class RCAGateError(RuntimeError):
    """RCA Gate не вернул валидное решение."""


async def run_readiness_gate(
    *,
    conversation: AgentConversation,
    rca_input: RCAInput,
    investigation: RCAInvestigation,
) -> RCAReadinessDecision:
    """Проверяет готовность контекста до создания или редактирования draft."""
    system_message = llm_client.build_system_message(
        role_instruction=GATE_PROMPT,
        extra_context={
            "mode": "readiness",
            "rca_input": rca_input.model_dump(mode="json"),
            "investigation": investigation.model_dump(mode="json"),
        },
        output_contract="JSON RCAReadinessDecision.",
    )

    try:
        return await llm_client.ainvoke_structured(
            [
                system_message,
                *to_llm_messages(conversation),
                HumanMessage(
                    content=(
                        "Проверь, достаточно ли данных для продолжения RCA."
                    )
                ),
            ],
            RCAReadinessDecision,
            worker_kind="rca_gate",
        )
    except Exception as exc:
        raise RCAGateError("RCA readiness gate failed.") from exc


async def run_quality_gate(
    *,
    conversation: AgentConversation,
    rca_input: RCAInput,
    investigation: RCAInvestigation,
    draft: RCAReportDraft,
    validation: RCAValidationResult,
) -> RCAQualityDecision:
    """Проверяет draft перед созданием новой версии RCA-справки."""
    system_message = llm_client.build_system_message(
        role_instruction=GATE_PROMPT,
        extra_context={
            "mode": "quality",
            "rca_input": rca_input.model_dump(mode="json"),
            "investigation": investigation.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "task_validation": validation.model_dump(mode="json"),
        },
        output_contract="JSON RCAQualityDecision.",
    )

    try:
        return await llm_client.ainvoke_structured(
            [
                system_message,
                *to_llm_messages(conversation),
                HumanMessage(
                    content="Проверь качество draft RCA перед сохранением."
                ),
            ],
            RCAQualityDecision,
            worker_kind="rca_gate",
        )
    except Exception as exc:
        raise RCAGateError("RCA quality gate failed.") from exc