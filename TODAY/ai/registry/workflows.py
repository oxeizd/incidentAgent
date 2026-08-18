from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.ai.registry.payloads import PAYLOAD_SCHEMAS
from app.ai.schemas.payloads import PayloadSchema


ConstraintCheck = Callable[
    [dict[str, Any], dict[str, Any]],
    tuple[bool, str | None],
]


def has_context_key(key: str) -> ConstraintCheck:
    """
    Требует truthy значение в input_context.
    """

    def check(
        orchestrator_state: dict[str, Any],
        input_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        if input_context.get(key):
            return True, None

        return False, f"Missing required input_context key: {key}"

    return check


def worker_dependency(
    prior_kind: str,
) -> ConstraintCheck:
    """
    Требует непосредственного завершённого parent worker-а указанного kind.
    """

    def check(
        orchestrator_state: dict[str, Any],
        input_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        parent_worker_id = input_context.get("parent_worker_id")

        if not parent_worker_id:
            return (
                False,
                f"Requires completed parent worker of kind {prior_kind!r}",
            )

        workers = orchestrator_state.get("workers") or {}
        parent = workers.get(parent_worker_id)

        if parent is None:
            return (
                False,
                f"Parent worker {parent_worker_id!r} was not found",
            )

        if parent.get("kind") != prior_kind:
            return (
                False,
                f"Parent worker must have kind {prior_kind!r}",
            )

        if parent.get("status") != "done":
            return (
                False,
                f"Parent worker {parent_worker_id!r} is not completed",
            )

        return True, None

    return check


def artifact_dependency(
    artifact_kind: str,
    *,
    min_version: int = 0,
) -> ConstraintCheck:
    """
    Требует artifact указанного kind и минимальной version.

    artifact_id обязан быть передан в input_context.
    """

    def check(
        orchestrator_state: dict[str, Any],
        input_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        artifact_id = input_context.get("artifact_id")

        if not artifact_id:
            return (
                False,
                f"Requires artifact_id for artifact kind {artifact_kind!r}",
            )

        artifacts = orchestrator_state.get("artifacts") or {}
        artifact = artifacts.get(artifact_id)

        if artifact is None:
            return (
                False,
                f"Artifact {artifact_id!r} was not found",
            )

        if artifact.get("kind") != artifact_kind:
            return (
                False,
                f"Artifact {artifact_id!r} must have kind {artifact_kind!r}",
            )

        current_version = artifact.get("current_version", -1)

        if current_version < min_version:
            return (
                False,
                f"Artifact {artifact_id!r} must be at least version "
                f"{min_version}",
            )

        return True, None

    return check


def any_of(
    constraints: list[ConstraintCheck],
    *,
    description: str = "",
) -> ConstraintCheck:
    """
    Успех, если хотя бы один constraint проходит.
    """

    if not constraints:
        raise ValueError("any_of requires at least one constraint")

    def check(
        orchestrator_state: dict[str, Any],
        input_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        reasons: list[str] = []

        for constraint in constraints:
            ok, reason = constraint(
                orchestrator_state,
                input_context,
            )

            if ok:
                return True, None

            if reason:
                reasons.append(reason)

        return (
            False,
            description or " OR ".join(reasons),
        )

    return check


@dataclass(frozen=True, slots=True)
class RegisteredWorkflow:
    """
    Спецификация worker workflow.

    entry_node — имя первой node внутри compiled subgraph.
    payload_schema — единственный source of truth для worker payload.
    """

    kind: str
    entry_node: str
    payload_schema: type[PayloadSchema]

    clears_history_on_success: bool = True
    default_max_rounds: int = 5

    constraints: list[ConstraintCheck] = field(default_factory=list)

    description: str = ""
    icon: str = ""

    def validate_preconditions(
        self,
        orchestrator_state: dict[str, Any],
        input_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        for constraint in self.constraints:
            ok, reason = constraint(
                orchestrator_state,
                input_context,
            )

            if not ok:
                return False, reason

        return True, None


WORKFLOW_REGISTRY: dict[str, RegisteredWorkflow] = {}


def register_workflow(spec: RegisteredWorkflow) -> None:
    """
    Регистрирует worker и его payload schema одной операцией.

    Нельзя зарегистрировать workflow без payload schema или поменять payload
    schema под тем же kind скрытно.
    """
    if spec.kind in WORKFLOW_REGISTRY:
        raise ValueError(
            f"Workflow {spec.kind!r} is already registered"
        )

    existing_schema = PAYLOAD_SCHEMAS.get(spec.kind)

    if (
        existing_schema is not None
        and existing_schema is not spec.payload_schema
    ):
        raise ValueError(
            f"Payload schema for {spec.kind!r} is already registered "
            "with another class"
        )

    WORKFLOW_REGISTRY[spec.kind] = spec
    PAYLOAD_SCHEMAS[spec.kind] = spec.payload_schema


# Backward-compatible names для лаконичной bootstrap-конфигурации.
WorkflowConstraint = ConstraintCheck
HasContextKey = has_context_key
WorkerDependency = worker_dependency
ArtifactDependency = artifact_dependency
AnyOfConstraint = any_of