from __future__ import annotations
from typing import Literal, Any, Optional
from typing_extensions import TypedDict, NotRequired
from pydantic import BaseModel


class SectionPatch(BaseModel):
    section: str
    original_value: Any
    new_value: Any
    operation: Literal["update", "add", "delete"]
    applied_by_worker_id: str
    timestamp: str
    note: str = ""


class ArtifactVersion(TypedDict):
    version: int
    sections: dict[str, Any]
    produced_by_worker_id: str
    patches: NotRequired[list[dict]]
    note: str
    timestamp: str


class Artifact(TypedDict):
    id: str
    kind: str
    status: Literal["draft", "final", "archived"]
    versions: list[ArtifactVersion]
    current_version: int
    locked_for_editing: bool
    required_by: NotRequired[list[str]]
    created_by_worker_id: str
    created_at: str