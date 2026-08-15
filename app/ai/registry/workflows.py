from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING
from app.ai.schemas.payloads import PayloadSchema
from app.ai.registry.payloads import PAYLOAD_SCHEMAS

if TYPE_CHECKING:
    from app.ai.schemas.orchestrator import OrchestratorState

"""
ИСПРАВЛЕНО: WorkflowConstraint раньше был базовым классом с четырьмя
сабклассами (ArtifactDependency/WorkerDependency/HasContextKey/AnyOfConstraint),
переопределяющими check() — единственное место в проекте, где дистрибуция
"что сделать для этого случая" построена на наследовании классов, тогда как
везде остальное (WORKFLOW_REGISTRY, PAYLOAD_SCHEMAS, INTENT_REGISTRY,
app/ai/graph/orchestrator.py: _FINALIZERS/_ARTIFACT_HOOKS) — обычные dict/
функции. теперь констрейнт — просто функция (orchestrator_state, input_context)
-> (bool, reason|None); параметризованные констрейнты — фабрики, возвращающие
такую функцию через замыкание (тот же принцип, что уже применяется в
app/ai/runtime/node_kit.py для деривации типа поля формы).

Старые имена (ArtifactDependency, WorkerDependency, HasContextKey,
AnyOfConstraint) оставлены как алиасы новых фабрик с ТЕМИ ЖЕ сигнатурами
вызова — app/ai/registry/bootstrap.py вызывает их как раньше
(`ArtifactDependency(artifact_kind=..., min_version=...)` и т.п.) и не
нуждается в правках; расходится только то, что "внутри" — раньше это
были экземпляры классов с методом .check(), теперь просто функции из фабрик ниже.
"""

ConstraintCheck = Callable[[Any, dict], tuple]

# Deprecated: имя оставлено для совместимости с существующими импортами
# (app/ai/registry/__init__.py) — раньше это был базовый класс, теперь просто
# алиас типа Callable, которому соответствует любой констрейнт из фабрик ниже.
WorkflowConstraint = ConstraintCheck


def artifact_dependency(artifact_kind: str, min_version: int = 1) -> ConstraintCheck:
    def check(orchestrator_state, input_context):
        artifact_id = input_context.get("artifact_id")
        artifact = orchestrator_state["artifacts"].get(artifact_id) if artifact_id else None
        if not artifact or artifact["kind"] != artifact_kind:
            return False, f"Requires an '{artifact_kind}' artifact (artifact_id in input_context)"
        if artifact["current_version"] < min_version:
            return False, f"Artifact '{artifact_id}' must be at least v{min_version}"
        return True, None
    return check


def worker_dependency(prior_kind: str) -> ConstraintCheck:
    def check(orchestrator_state, input_context):
        parent_id = input_context.get("parent_worker_id")
        parent = orchestrator_state["workers"].get(parent_id) if parent_id else None
        if not parent or parent["kind"] != prior_kind or parent["status"] != "done":
            return False, f"Requires a completed '{prior_kind}' as direct parent"
        return True, None
    return check


def has_context_key(key: str) -> ConstraintCheck:
    def check(orchestrator_state, input_context):
        if input_context.get(key):
            return True, None
        return False, f"Missing '{key}' in input_context"
    return check


def any_of(constraints: list, description: str = "") -> ConstraintCheck:
    def check(orchestrator_state, input_context):
        reasons = []
        for c in constraints:
            ok, reason = c(orchestrator_state, input_context)
            if ok:
                return True, None
            reasons.append(reason)
        return False, description or " ИЛИ ".join(r for r in reasons if r)
    return check


# Алиасы для обратной совместимости вызовов (см. докстринг выше).
ArtifactDependency = artifact_dependency
WorkerDependency = worker_dependency
HasContextKey = has_context_key
AnyOfConstraint = any_of


@dataclass
class RegisteredWorkflow:
    kind: str
    entry_node: str
    payload_schema: type[PayloadSchema]
    clears_history_on_success: bool = True
    default_max_rounds: int = 5
    constraints: list[ConstraintCheck] = field(default_factory=list)
    description: str = ""
    icon: str = ""

    def validate_preconditions(self, orchestrator_state, input_context: dict):
        for constraint in self.constraints:
            ok, reason = constraint(orchestrator_state, input_context)
            if not ok:
                return False, reason
        return True, None


WORKFLOW_REGISTRY: dict[str, RegisteredWorkflow] = {}


def register_workflow(spec: RegisteredWorkflow) -> None:
    """
    Единая точка регистрации workflow: заводит запись и в WORKFLOW_REGISTRY,
    и в PAYLOAD_SCHEMAS (app/ai/registry/payloads.py) ЗА ОДИН вызов.

    РАНЬШЕ payload-схема регистрировалась отдельным вызовом
    register_payload_schema(kind, Schema) в payloads.py, независимо от
    register_workflow(kind=..., payload_schema=Schema) здесь — одни и те же
    данные задавались в двух разных файлах и могли рассинхронизироваться:
    добавили workflow здесь и забыли зарегистрировать для него payload —
    тогда spawn_worker() из runtime/factory.py упал бы на validate_payload()
    с 'Unknown workflow kind', хотя WORKFLOW_REGISTRY уже знает про такой kind.
    теперь второй регистр невозможен физически: он строится из spec.payload_schema.
    """
    if spec.kind in WORKFLOW_REGISTRY:
        raise ValueError(f"workflow '{spec.kind}' already registered")
    if spec.kind in PAYLOAD_SCHEMAS and PAYLOAD_SCHEMAS[spec.kind] is not spec.payload_schema:
        raise ValueError(
            f"payload schema for '{spec.kind}' already registered with a different schema "
            f"({PAYLOAD_SCHEMAS[spec.kind]!r} vs {spec.payload_schema!r})"
        )
    WORKFLOW_REGISTRY[spec.kind] = spec
    PAYLOAD_SCHEMAS[spec.kind] = spec.payload_schema
