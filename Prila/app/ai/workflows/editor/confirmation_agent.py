from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.ai.workflows.editor.contracts import EditProposal
from app.services.llm import llm_client


class ConfirmationDecision(BaseModel):
    """
    Семантическая интерпретация свободного ответа на preview правки.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "confirmed",
        "declined",
        "unclear",
    ]
    clarification: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )


_CONFIRMATION_PROMPT = """
Ты интерпретируешь ответ пользователя на preview изменения RCA-справки.

Тебе передан preview правки и обычный текстовый ответ пользователя.

Верни:
- action="confirmed", если пользователь ясно разрешает применить именно
  показанный preview;
- action="declined", если пользователь ясно отказывается от правки;
- action="unclear", если пользователь задаёт вопрос, просит другой вариант,
  меняет инструкцию, отвечает неоднозначно или не подтверждает явно.

Для action="unclear" заполни clarification коротким вопросом:
«Применить показанную правку? Ответьте “да” или “нет”.»

Не считай подтверждением:
- просьбу изменить preview;
- вопрос о последствиях;
- фразу, не относящуюся явно к применению preview;
- новую инструкцию на редактирование.

Верни только JSON строго по схеме ConfirmationDecision.
"""


async def interpret_editor_confirmation(
    *,
    proposal: EditProposal,
    user_text: str,
) -> bool:
    """
    Возвращает bool только для однозначного decision.

    При unclear бросает ValueError, который EditorWorkflow уже превращает
    в понятный повторный вопрос без применения side effect.
    """
    system = llm_client.build_system_message(
        role_instruction=_CONFIRMATION_PROMPT,
        extra_context={
            "preview": proposal.model_dump(mode="json"),
        },
        output_contract="JSON строго по схеме ConfirmationDecision.",
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        ConfirmationDecision,
        worker_kind="editor_confirmation",
    )

    if decision.action == "confirmed":
        return True

    if decision.action == "declined":
        return False

    raise ValueError(
        decision.clarification
        or "Применить показанную правку? Ответьте «да» или «нет»."
    )