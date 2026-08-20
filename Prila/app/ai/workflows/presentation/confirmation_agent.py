from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.memory.artifacts.presentations.document import (
    PresentationDocument,
)
from app.services.llm import llm_client


class PresentationConfirmationDecision(BaseModel):
    """
    Интерпретация обычного текстового ответа на preview презентации.

    Workflow сохраняет draft только при action="confirmed".
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["confirmed", "declined", "unclear"]
    clarification: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
    )


_CONFIRMATION_PROMPT = """
Ты интерпретируешь ответ пользователя на вопрос о сохранении черновика
презентации IT-инцидента.

Тебе переданы preview PresentationDocument и текст последнего ответа
пользователя.

Выбери одно действие:

- action="confirmed", если пользователь явно согласился сохранить
  показанный черновик презентации.

- action="declined", если пользователь явно отказался от сохранения,
  отменил создание или просит не сохранять черновик.

- action="unclear", если пользователь:
  - добавляет или исправляет факты;
  - просит изменить содержание, структуру или детализацию;
  - задаёт вопрос вместо ответа;
  - отвечает неоднозначно;
  - использует фразы вроде «давай поправим», «ещё добавь», «не так»,
    «покажи подробнее».

Правила:

- Не считай согласие на доработку согласием на сохранение.
- Не считай «давай», «окей» или «нормально» подтверждением, если из
  контекста не следует однозначное согласие именно на сохранение.
- При малейшей неоднозначности выбирай action="unclear".
- Для action="unclear" обязательно верни короткий вопрос clarification,
  например: «Сохранить текущий черновик или сначала внести изменения?»
- Для action="confirmed" и action="declined" clarification должен быть null.

Возвращай только JSON строго по схеме PresentationConfirmationDecision.
"""


async def interpret_presentation_confirmation(
    *,
    document: PresentationDocument,
    user_text: str,
) -> bool:
    """
    Возвращает:
    - True: пользователь подтвердил сохранение;
    - False: пользователь отказался.

    При неоднозначном тексте бросает ValueError с безопасным вопросом,
    который workflow показывает пользователю, оставляя pending confirm.
    """
    system = llm_client.build_system_message(
        role_instruction=_CONFIRMATION_PROMPT,
        extra_context={
            "presentation_document": document.model_dump(
                mode="json"
            ),
        },
        output_contract=(
            "JSON строго по схеме "
            "PresentationConfirmationDecision."
        ),
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        PresentationConfirmationDecision,
        worker_kind="presentation_confirmation",
    )

    if decision.action == "confirmed":
        return True

    if decision.action == "declined":
        return False

    raise ValueError(
        decision.clarification
        or (
            "Сохранить текущий черновик презентации "
            "или сначала внести изменения?"
        )
    )