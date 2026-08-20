from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TextResume(BaseModel):
    """
    Обычный текстовый ответ пользователя на pending Interaction.

    Пока UI не использует structured controls, API передаёт только text.
    Какая именно семантика у текста — решает owner workflow через LLM,
    опираясь на Interaction и task snapshot.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)