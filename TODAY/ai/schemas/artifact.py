from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict


ArtifactKind = Literal[
    "incident_report",
    "presentation_reference",
]

ArtifactStatus = Literal[
    "draft",
    "final",
    "archived",
]

PatchOperation = Literal[
    "update",
    "add",
    "delete",
]


class SectionPatch(BaseModel):
    """
    Описание одного изменения секции versioned artifact.

    Используется editor-worker. Сам artifact остаётся неизменяемым:
    после изменения создаётся новая ArtifactVersion.
    """

    section: str = Field(min_length=1)
    original_value: Any
    new_value: Any
    operation: PatchOperation
    applied_by_worker_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    note: str = ""


class ArtifactVersion(TypedDict):
    version: int
    sections: dict[str, Any]
    produced_by_worker_id: str
    note: str
    timestamp: str
    patches: NotRequired[list[dict[str, Any]]]


class Artifact(TypedDict):
    """
    Runtime artifact в LangGraph state.

    `incident_report` хранит локальный versioned RCA результат:
      - analysis: str
      - tasks: list[dict]
      - _rca_input: dict

    `presentation_reference` хранит только ссылку/минимальный snapshot
    PresentationDocument, который канонически сохранён в memory БД.
    HTML здесь никогда не хранится.
    """

    id: str
    kind: ArtifactKind
    status: ArtifactStatus
    versions: list[ArtifactVersion]
    current_version: int
    locked_for_editing: bool
    created_by_worker_id: str
    created_at: str
    required_by: NotRequired[list[str]]