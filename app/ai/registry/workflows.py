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
app/ai/graph/orchestrator/dispatcher.py) — обычные dict/функции. теперь
констрейнт — просто функция (orchestrator_state, input_context) ->
(bool, reason|None); параметризованные констрейнты — фабрики, возвращающие
такую функцию через замыкание (тот же принцип, что уже применяется в
app/ai/runtime/node_kit.py для деривации типа поля формы).

Старые имена (ArtifactDependency, WorkerDependency, HasContextKey,
AnyOfConstraint) оставлены как алиасы новых фабрик с ТЕМИ ЖЕ сигнатурами
вызова — app/ai/registry/bootstrap.py вызывает их как раньше
(`ArtifactDependency(artifact_kind=..., min_version=...)` и т.п.) и не
нуждается в правках; расходится только то, что "внутри" — раньше это
были экземпляры классов с методом .check(), теперь просто функции из фабрик ниже.

Wave 2 (idempotent bootstrap): RegisteredWorkflow стал frozen — immutable
config после регистрации. register_workflow() стал идемпотентным: повторная
регистрация ТОГО ЖЕ (по значению) workflow — no-op; регистрация другого
определения под тем же kind — по-прежнему ValueError. Это делает
register_builtin_workflows() безопасным для повторного вызова (повторный
FastAPI lifespan, несколько app instances в тестах, local reload).
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


@dataclass(frozen=True)
class RegisteredWorkflow:
    """
    Wave 2: frozen=True — конфигурация workflow неизменяема после
    регистрации (см. Definition of Done: "Workflow module owns its graph,
    payload contract and result handler"). constraints — tuple, не list:
    в frozen dataclass поле нельзя переприсвоить, и tuple явно фиксирует,
    что список констрейнтов тоже не редактируется постфактум.
    """

    kind: str
    entry_node: str
    payload_schema: type[PayloadSchema]
    clears_history_on_success: bool = True
    default_max_rounds: int = 5
    constraints: tuple[ConstraintCheck, ...] = field(default_factory=tuple)
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

    Wave 2 (idempotent bootstrap): если под этим kind уже зарегистрирован
    ЭКВИВАЛЕНТНЫЙ по значению spec (все поля dataclass совпадают, включая
    сравнение constraints-функций по идентичности при повторной передаче
    того же модульного объекта) — вызов no-op, ничего не падает. Если
    зарегистрирован kind с ДРУГИМ определением — ValueError, как и раньше.

    РАНЬШЕ payload-схема регистрировалась отдельным вызовом
    register_payload_schema(kind, Schema) в payloads.py, независимо от
    register_workflow(kind=..., payload_schema=Schema) здесь — одни и те же
    данные задавались в двух разных файлах и могли рассинхронизироваться.
    теперь второй регистр невозможен физически: он строится из spec.payload_schema.
    """
    existing = WORKFLOW_REGISTRY.get(spec.kind)
    if existing is not None:
        if existing == spec:
            return
        raise ValueError(f"workflow '{spec.kind}' already registered with a different definition")

    if spec.kind in PAYLOAD_SCHEMAS and PAYLOAD_SCHEMAS[spec.kind] is not spec.payload_schema:
        raise ValueError(
            f"payload schema for '{spec.kind}' already registered with a different schema "
            f"({PAYLOAD_SCHEMAS[spec.kind]!r} vs {spec.payload_schema!r})"
        )
    WORKFLOW_REGISTRY[spec.kind] = spec
    PAYLOAD_SCHEMAS[spec.kind] = spec.payload_schema
