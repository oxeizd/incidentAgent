from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=500,
    )


class ThreadResponse(BaseModel):
    id: str
    title: str | None = None
    created_at: str
    updated_at: str | None = None


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ThreadMessage(BaseModel):
    id: str
    role: MessageRole
    content: str
    artifact: dict[str, Any] | None = None
    created_at: str


class ConversationTurnRequest(BaseModel):
    """
    Один текстовый ход в ConversationState.

    Ответ на Interaction передаётся тем же text-полем. Guard и workflow
    определяют, является ли текст ответом на ожидающий вопрос, уточнением,
    новой задачей, отменой или обычным диалогом.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=20_000,
    )

    @model_validator(mode="after")
    def normalize_text(self) -> "ConversationTurnRequest":
        self.text = self.text.strip()

        if not self.text:
            raise ValueError(
                "text must not be empty"
            )

        return self


class ConversationTaskResponse(BaseModel):
    """
    Компактный публичный snapshot active/suspended task.

    Не публикует внутреннюю agent history, source snapshots, raw RCA,
    artifact sections или runtime implementation details.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    kind: Literal[
        "search",
        "rca",
        "edit",
        "presentation",
    ]
    goal: str
    status: Literal[
        "active",
        "awaiting_input",
        "suspended",
        "completed",
        "cancelled",
        "failed",
    ]
    stage: str

    refs: list[dict[str, Any]] = Field(
        default_factory=list,
    )

    suspended_at: str | None = None
    suspension_reason: str | None = None


class PendingInteractionResponse(BaseModel):
    """
    Публичный transport contract interaction.

    UI возвращает interaction_id при structured input в будущем. Пока
    текстовый клиент может отправлять только text через turns endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    interaction_id: str
    owner: Literal[
        "search",
        "rca",
        "edit",
        "presentation",
    ]
    continuation_stage: str

    kind: Literal[
        "free_text",
        "confirm",
        "single_select",
        "multi_select",
        "form",
    ]
    question: str

    options: list[dict[str, Any]] = Field(
        default_factory=list,
    )
    fields: list[dict[str, Any]] = Field(
        default_factory=list,
    )

    preview: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )


class ConversationTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    task: ConversationTaskResponse | None = None
    suspended_task: ConversationTaskResponse | None = None
    pending_interaction: PendingInteractionResponse | None = None
    artifact: dict[str, Any] | None = None
    finish_reason: Literal["stop", "requires_input"]