from __future__ import annotations

from app.ai.registry.workflows import (
    AnyOfConstraint,
    ArtifactDependency,
    HasContextKey,
    RegisteredWorkflow,
    WORKFLOW_REGISTRY,
    register_workflow,
)
from app.ai.schemas.payloads import (
    CreatorPayload,
    EditorPayload,
    RCAPayload,
    SearchPayload,
)


def register_builtin_workflows() -> None:
    """
    Idempotent registration встроенных worker workflows.

    Повторный вызов допускается только если workflow уже существует как
    ожидаемый built-in kind. Это нужно для test lifespan и development reload.
    """
    if "search" not in WORKFLOW_REGISTRY:
        register_workflow(
            RegisteredWorkflow(
                kind="search",
                entry_node="resolve_entity",
                payload_schema=SearchPayload,
                clears_history_on_success=True,
                default_max_rounds=3,
                description=(
                    "Resolve entities and retrieve incidents/assignments "
                    "for user search or downstream RCA."
                ),
                icon="🔎",
            )
        )

    if "rca" not in WORKFLOW_REGISTRY:
        register_workflow(
            RegisteredWorkflow(
                kind="rca",
                entry_node="rca_gate",
                payload_schema=RCAPayload,
                clears_history_on_success=True,
                default_max_rounds=5,
                constraints=[
                    AnyOfConstraint(
                        [
                            HasContextKey("incident_number"),
                            HasContextKey("raw_description"),
                            HasContextKey("search_summary"),
                        ],
                        description=(
                            "Для RCA обязателен один источник контекста: "
                            "номер инцидента, свободное описание проблемы "
                            "или результаты поиска."
                        ),
                    )
                ],
                description=(
                    "RCA по номеру инцидента, описанию проблемы или "
                    "результатам поиска: gate, уточняющие вопросы, "
                    "анализ и валидация системных мер."
                ),
                icon="🔬",
            )
        )

    if "editor" not in WORKFLOW_REGISTRY:
        register_workflow(
            RegisteredWorkflow(
                kind="editor",
                entry_node="apply_edit",
                payload_schema=EditorPayload,
                clears_history_on_success=True,
                default_max_rounds=2,
                constraints=[
                    ArtifactDependency(
                        "incident_report",
                        min_version=0,
                    )
                ],
                description=(
                    "Edit versioned incident report analysis or one "
                    "systemic measure."
                ),
                icon="✏️",
            )
        )

    if "creator" not in WORKFLOW_REGISTRY:
        register_workflow(
            RegisteredWorkflow(
                kind="creator",
                entry_node="collect_fields",
                payload_schema=CreatorPayload,
                clears_history_on_success=True,
                default_max_rounds=5,
                constraints=[
                    AnyOfConstraint(
                        [
                            HasContextKey("artifact_sections"),
                            HasContextKey("incident_number"),
                            HasContextKey("source_text"),
                        ],
                        description=(
                            "Для презентации нужен RCA-отчёт, номер "
                            "инцидента или описание."
                        ),
                    )
                ],
                description=(
                    "Build PresentationDocument. Best input is a ready RCA "
                    "report together with incident_number; supports "
                    "standalone creation from incident number or user "
                    "description."
                ),
                icon="🖥️",
            )
        )