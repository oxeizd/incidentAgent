from __future__ import annotations
from datetime import datetime
from typing import Any
from app.ai.schemas.artifact import Artifact, ArtifactVersion, SectionPatch


def apply_patches_to_artifact(artifact: Artifact, patches: list[SectionPatch]) -> Artifact:
    if not patches:
        raise ValueError("apply_patches_to_artifact called with an empty patch list")
    if artifact.get("locked_for_editing"):
        raise ValueError(f"Artifact '{artifact['id']}' is locked for editing (status={artifact['status']})")
    current = artifact["versions"][artifact["current_version"]]
    new_sections: dict[str, Any] = dict(current["sections"])
    for patch in patches:
        if patch.operation == "update":
            new_sections[patch.section] = patch.new_value
        elif patch.operation == "add":
            new_sections.setdefault(patch.section, patch.new_value)
        elif patch.operation == "delete":
            new_sections.pop(patch.section, None)
    new_version: ArtifactVersion = {
        "version": artifact["current_version"] + 1, "sections": new_sections,
        "produced_by_worker_id": patches[0].applied_by_worker_id,
        "patches": [p.model_dump() for p in patches],
        "note": f"Applied {len(patches)} patch(es)", "timestamp": datetime.now().isoformat(),
    }
    artifact["versions"].append(new_version)
    artifact["current_version"] += 1
    return artifact


def replace_artifact_sections(
    artifact: Artifact, updated_sections: dict[str, Any], *, produced_by_worker_id: str, note: str,
) -> Artifact:
    """
    Версионирует артефакт, заменяя ЦЕЛИКОМ указанные секции (не патч одного
    текстового поля). updated_sections мержится поверх текущих секций —
    секции, не упомянутые в updated_sections, остаются как были.
    Используется для reanalyze (analysis+tasks+_rca_input разом) и для
    замены одной задачи в tasks целиком (editor::_replace_task).
    """
    if artifact.get("locked_for_editing"):
        raise ValueError(f"Artifact '{artifact['id']}' is locked for editing (status={artifact['status']})")
    current = artifact["versions"][artifact["current_version"]]
    new_sections: dict[str, Any] = {**current["sections"], **updated_sections}
    new_version: ArtifactVersion = {
        "version": artifact["current_version"] + 1, "sections": new_sections,
        "produced_by_worker_id": produced_by_worker_id,
        "note": note, "timestamp": datetime.now().isoformat(),
    }
    artifact["versions"].append(new_version)
    artifact["current_version"] += 1
    return artifact


def create_artifact(artifact_id: str, kind: str, initial_sections: dict, created_by_worker_id: str) -> Artifact:
    now = datetime.now().isoformat()
    return {
        "id": artifact_id, "kind": kind, "status": "draft",
        "versions": [{"version": 0, "sections": initial_sections, "produced_by_worker_id": created_by_worker_id,
                      "note": "initial draft", "timestamp": now}],
        "current_version": 0, "locked_for_editing": False,
        "created_by_worker_id": created_by_worker_id, "created_at": now,
    }