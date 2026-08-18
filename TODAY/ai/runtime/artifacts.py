from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.ai.schemas.artifact import Artifact, ArtifactVersion, SectionPatch


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_artifact(
    *,
    artifact_id: str,
    kind: str,
    initial_sections: dict[str, Any],
    created_by_worker_id: str,
    status: str = "draft",
) -> Artifact:
    """
    Создаёт runtime versioned artifact с первой версией 0.

    Не мутирует входной initial_sections.
    """
    now = utc_now_iso()

    first_version: ArtifactVersion = {
        "version": 0,
        "sections": deepcopy(initial_sections),
        "produced_by_worker_id": created_by_worker_id,
        "note": "Initial draft",
        "timestamp": now,
    }

    return {
        "id": artifact_id,
        "kind": kind,
        "status": status,
        "versions": [first_version],
        "current_version": 0,
        "locked_for_editing": False,
        "created_by_worker_id": created_by_worker_id,
        "created_at": now,
    }


def current_sections(
    artifact: Artifact,
) -> dict[str, Any]:
    """
    Возвращает deep copy sections текущей версии.
    """
    version_index = artifact["current_version"]

    if version_index < 0 or version_index >= len(artifact["versions"]):
        raise ValueError(
            f"Artifact {artifact['id']!r} has invalid current_version "
            f"{version_index}"
        )

    return deepcopy(
        artifact["versions"][version_index]["sections"]
    )


def apply_patches_to_artifact(
    artifact: Artifact,
    patches: list[SectionPatch],
    *,
    note: str | None = None,
) -> Artifact:
    """
    Применяет section patches, создавая новую artifact version.

    Возвращает новый dict artifact, исходный state object не мутирует.
    """
    if not patches:
        raise ValueError(
            "apply_patches_to_artifact requires at least one patch"
        )

    if artifact.get("locked_for_editing"):
        raise ValueError(
            f"Artifact {artifact['id']!r} is locked for editing"
        )

    cloned = deepcopy(artifact)
    sections = current_sections(cloned)

    for patch in patches:
        if patch.operation == "update":
            sections[patch.section] = deepcopy(patch.new_value)
        elif patch.operation == "add":
            if patch.section in sections:
                raise ValueError(
                    f"Section {patch.section!r} already exists in artifact "
                    f"{artifact['id']!r}"
                )
            sections[patch.section] = deepcopy(patch.new_value)
        elif patch.operation == "delete":
            sections.pop(patch.section, None)
        else:
            raise ValueError(
                f"Unsupported patch operation: {patch.operation!r}"
            )

    _append_version(
        artifact=cloned,
        sections=sections,
        produced_by_worker_id=patches[0].applied_by_worker_id,
        note=note or f"Applied {len(patches)} patch(es)",
        patches=[patch.model_dump(mode="json") for patch in patches],
    )

    return cloned


def replace_artifact_sections(
    artifact: Artifact,
    updated_sections: dict[str, Any],
    *,
    produced_by_worker_id: str,
    note: str,
) -> Artifact:
    """
    Создаёт новую версию artifact, заменяя целиком указанные sections.

    Нужен для:
      - reanalyze: analysis + tasks + _rca_input;
      - editor: замена одной структурной меры внутри tasks;
      - будущих bulk edits.
    """
    if artifact.get("locked_for_editing"):
        raise ValueError(
            f"Artifact {artifact['id']!r} is locked for editing"
        )

    cloned = deepcopy(artifact)
    sections = current_sections(cloned)
    sections.update(deepcopy(updated_sections))

    _append_version(
        artifact=cloned,
        sections=sections,
        produced_by_worker_id=produced_by_worker_id,
        note=note,
    )

    return cloned


def _append_version(
    *,
    artifact: Artifact,
    sections: dict[str, Any],
    produced_by_worker_id: str,
    note: str,
    patches: list[dict[str, Any]] | None = None,
) -> None:
    next_version = artifact["current_version"] + 1

    version: ArtifactVersion = {
        "version": next_version,
        "sections": sections,
        "produced_by_worker_id": produced_by_worker_id,
        "note": note,
        "timestamp": utc_now_iso(),
    }

    if patches:
        version["patches"] = patches

    artifact["versions"].append(version)
    artifact["current_version"] = next_version