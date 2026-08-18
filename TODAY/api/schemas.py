from __future__ import annotations

import json
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


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ToolOutput(BaseModel):
    tool_call_id: str = Field(min_length=1)
    output: dict[str, Any] | str | bool


class CreateRunRequest(BaseModel):
    """
    Один conversation turn.

    Для нового текста используется `text`.
    Для resume native LangGraph interrupt используется ровно один tool_output.
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    tool_outputs: list[ToolOutput] | None = None

    @model_validator(mode="after")
    def validate_turn(self) -> "CreateRunRequest":
        normalized_text = (
            self.text.strip()
            if isinstance(self.text, str)
            else ""
        )

        has_text = bool(normalized_text)
        has_tool_outputs = bool(self.tool_outputs)

        if has_text == has_tool_outputs:
            raise ValueError(
                "Provide exactly one of text or tool_outputs"
            )

        if has_text:
            self.text = normalized_text
            return self

        if (
            self.tool_outputs is None
            or len(self.tool_outputs) != 1
        ):
            raise ValueError(
                "Provide exactly one tool output"
            )

        return self


class RunEventType(str, Enum):
    RUN_STARTED = "run.started"
    RUN_PROGRESS = "run.progress"
    ARTIFACT_CREATED = "artifact.created"
    RUN_REQUIRES_ACTION = "run.requires_action"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class RunEvent(BaseModel):
    schema_version: Literal[1] = 1

    event_id: str
    thread_id: str
    run_id: str

    sequence: int = Field(ge=1)
    timestamp: str

    type: RunEventType
    data: dict[str, Any] = Field(default_factory=dict)


class RunError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None


def interaction_to_tool_call(
    interaction: dict[str, Any],
) -> ToolCall:
    """
    Конвертирует LangGraph interrupt payload в OpenAI-like tool call.

    Native payload:
    {
      "interaction_id": "worker-id:round",
      "worker_id": "...",
      "question": "...",
      "type": "question" | "confirmation" | "form",
      "options": [...],
      "fields": [...],
      "metadata": {...}
    }
    """
    interaction_type = str(
        interaction.get("type")
        or interaction.get("kind")
        or "question"
    )

    function_name = {
        "confirmation": "ask_confirmation",
        "form": "ask_form",
    }.get(interaction_type, "ask_user")

    interaction_id = str(
        interaction.get("interaction_id")
        or interaction.get("id")
        or interaction.get("worker_id")
        or "pending"
    )

    arguments: dict[str, Any] = {
        "question": str(
            interaction.get("question")
            or "Уточните, пожалуйста, данные."
        ),
        "interaction_type": interaction_type,
        "worker_id": interaction.get("worker_id"),
        "round": interaction.get("round"),
    }

    for key in (
        "options",
        "fields",
        "metadata",
    ):
        if key in interaction:
            arguments[key] = interaction[key]

    return ToolCall(
        id=f"call_{interaction_id}",
        function=FunctionCall(
            name=function_name,
            arguments=json.dumps(
                arguments,
                ensure_ascii=False,
            ),
        ),
    )