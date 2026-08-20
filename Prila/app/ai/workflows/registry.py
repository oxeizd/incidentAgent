from __future__ import annotations

from app.ai.workflows.base import (
    WorkflowFactory,
    WorkflowRegistry,
    WorkflowRuntime,
)


_WORKFLOWS: WorkflowRegistry = {}


def register_workflow(
    kind: str,
) -> callable:
    """
    Регистрирует factory нового workflow.

    Factory используется вместо singleton instance: workflow должен быть
    stateless, а task-specific data лежит только в ConversationTask.snapshot.
    """

    def decorator(factory: WorkflowFactory) -> WorkflowFactory:
        if kind in _WORKFLOWS:
            raise ValueError(
                f"Workflow {kind!r} is already registered"
            )

        _WORKFLOWS[kind] = factory
        return factory

    return decorator


def get_workflow(
    kind: str,
) -> WorkflowRuntime:
    factory = _WORKFLOWS.get(kind)

    if factory is None:
        raise ValueError(
            f"Workflow {kind!r} is not registered"
        )

    return factory()


def registered_workflow_kinds() -> tuple[str, ...]:
    return tuple(_WORKFLOWS)