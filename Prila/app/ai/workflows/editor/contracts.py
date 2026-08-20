from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.workflows.rca.contracts import ProposedTask


EditableTextSection = Literal[
    "analysis",
    "summary",
    "root_cause",
    "impact",
    "timeline",
    "applied_measures",
    "open_questions",
    "limitations",
]

TaskOperation = Literal[
    "add",
    "replace",
    "remove",
]

EditAction = Literal[
    "edit_text",
    "edit_task",
    "clarify",
]


class EditIntentDecision(BaseModel):
    """
    Решение Editor Intent Agent-а.

    LLM только определяет тип правки и цель. Он не меняет artifact и не
    генерирует новый content на этой стадии.
    """

    model_config = ConfigDict(extra="forbid")

    action: EditAction
    instruction: str | None = None

    section: EditableTextSection | None = None

    task_operation: TaskOperation | None = None
    task_index: int | None = Field(default=None, ge=0)

    question: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "EditIntentDecision":
        if self.action == "edit_text":
            if not self.instruction or not self.section:
                raise ValueError(
                    "edit_text requires instruction and section"
                )

            if (
                self.task_operation is not None
                or self.task_index is not None
                or self.question is not None
            ):
                raise ValueError(
                    "edit_text cannot contain task fields/question"
                )

        elif self.action == "edit_task":
            if not self.instruction or not self.task_operation:
                raise ValueError(
                    "edit_task requires instruction and task_operation"
                )

            if self.task_operation in {"replace", "remove"}:
                if self.task_index is None:
                    raise ValueError(
                        "replace/remove require task_index"
                    )

            if self.section is not None or self.question is not None:
                raise ValueError(
                    "edit_task cannot contain section/question"
                )

        elif self.action == "clarify":
            if not self.question:
                raise ValueError(
                    "clarify requires question"
                )

            if any(
                value is not None
                for value in (
                    self.instruction,
                    self.section,
                    self.task_operation,
                    self.task_index,
                )
            ):
                raise ValueError(
                    "clarify may contain only question"
                )

        return self


class TextSectionProposal(BaseModel):
    """
    Preview правки одной text/list section.

    `new_value` не применяется автоматически: runtime покажет preview и
    сохранит его в pending editor task до explicit confirmation.
    """

    model_config = ConfigDict(extra="forbid")

    section: EditableTextSection
    original_value: Any
    new_value: Any
    rationale: str = Field(min_length=1, max_length=2_000)


class TaskEditProposal(BaseModel):
    """
    Preview операции над tasks.

    `task_index` — 0-based index для replace/remove; None для add.
    Для add/replace `new_task` обязателен, для remove — null.
    """

    model_config = ConfigDict(extra="forbid")

    operation: TaskOperation
    task_index: int | None = Field(default=None, ge=0)

    original_task: dict[str, Any] | None = None
    new_task: ProposedTask | None = None

    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_shape(self) -> "TaskEditProposal":
        if self.operation == "add":
            if self.task_index is not None:
                raise ValueError(
                    "add must not specify task_index"
                )

            if self.new_task is None:
                raise ValueError(
                    "add requires new_task"
                )

            if self.original_task is not None:
                raise ValueError(
                    "add must not contain original_task"
                )

        elif self.operation == "replace":
            if self.task_index is None:
                raise ValueError(
                    "replace requires task_index"
                )

            if self.new_task is None:
                raise ValueError(
                    "replace requires new_task"
                )

            if self.original_task is None:
                raise ValueError(
                    "replace requires original_task"
                )

        elif self.operation == "remove":
            if self.task_index is None:
                raise ValueError(
                    "remove requires task_index"
                )

            if self.original_task is None:
                raise ValueError(
                    "remove requires original_task"
                )

            if self.new_task is not None:
                raise ValueError(
                    "remove must not contain new_task"
                )

        return self


class EditProposal(BaseModel):
    """
    Итог preview от Editor Proposal Agent-а.

    Ровно один тип правки за одну user command. Это упрощает explicit
    confirm/persistence и гарантирует, что пользователь видит полный
    effect конкретного изменения.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "task"]
    text_change: TextSectionProposal | None = None
    task_change: TaskEditProposal | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "EditProposal":
        if self.kind == "text":
            if self.text_change is None:
                raise ValueError(
                    "text proposal requires text_change"
                )

            if self.task_change is not None:
                raise ValueError(
                    "text proposal cannot contain task_change"
                )

        if self.kind == "task":
            if self.task_change is None:
                raise ValueError(
                    "task proposal requires task_change"
                )

            if self.text_change is not None:
                raise ValueError(
                    "task proposal cannot contain text_change"
                )

        return self