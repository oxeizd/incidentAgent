from __future__ import annotations

import uuid
from typing import Any

from app.ai.schemas.conversation import (
    FormField,
    Interaction,
    InteractionKind,
    SelectOption,
    TaskKind,
)


def new_interaction_id(
    owner: TaskKind,
) -> str:
    """
    Уникальный ID вопроса.

    Он нужен уже сейчас, даже при текстовом UI: task хранит точный pending
    interaction, а позже API/UI сможет безопасно вернуть structured answer
    именно для этого вопроса.
    """
    return f"{owner}-interaction-{uuid.uuid4().hex[:12]}"


def build_interaction(
    *,
    owner: TaskKind,
    continuation_stage: str,
    kind: InteractionKind,
    question: str,
    options: list[SelectOption] | None = None,
    fields: list[FormField] | None = None,
    preview: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Interaction:
    """
    Factory валидного публичного interaction payload.

    Workflow передаёт только доменное содержание вопроса. Генерация ID и
    Pydantic validation централизованы здесь.
    """
    return Interaction(
        interaction_id=new_interaction_id(owner),
        owner=owner,
        continuation_stage=continuation_stage,
        kind=kind,
        question=question.strip(),
        options=options or [],
        fields=fields or [],
        preview=preview,
        metadata=metadata or {},
    )