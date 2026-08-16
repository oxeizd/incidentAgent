"""
app/ai/runtime/worker_runner.py

Wave 3: централизует технический execution flow spawn -> workflow graph
lookup -> graph.ainvoke -> typed outcome. Раньше это была функция
`_spawn_and_run()`, локальная для app/ai/graph/orchestrator.py — единственная
имплементация, но недоступная для тестирования отдельно от всего
orchestrator-модуля.

WorkerRunner НЕ знает о:
  - RCA report artifacts;
  - presentation artifacts;
  - intent routing;
  - UI interrupts;
  - edit domain логике.

Эти решения принимают intent handlers (app/ai/graph/orchestrator/handlers/*)
и artifact result handlers (app/ai/graph/orchestrator/artifacts/*) поверх
WorkerRunSuccess/WorkerRunFailure.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Sequence, TYPE_CHECKING, Union

from app.ai.runtime.factory import spawn_worker, SpawnError
from app.ai.schemas.worker import WorkerState

if TYPE_CHECKING:
    from app.ai.schemas.orchestrator import OrchestratorState

# CompiledWorkflow — результат StateGraph(...).compile(); не завязываемся на
# конкретный langgraph-тип здесь, чтобы не тянуть лишние импорты в модуль,
# который должен быть максимально тонким и легко тестируемым с фейковыми графами.
CompiledWorkflow = Any


@dataclass(frozen=True)
class WorkerRunSuccess:
    worker: WorkerState


@dataclass(frozen=True)
class WorkerRunFailure:
    error: SpawnError


WorkerRunOutcome = Union[WorkerRunSuccess, WorkerRunFailure]


class WorkerRunner:
    """Единственная реализация spawn -> graph lookup -> ainvoke."""

    def __init__(self, subgraphs: dict[str, CompiledWorkflow]):
        self._subgraphs = subgraphs

    async def run(
        self, kind: str, input_context: dict, state: "OrchestratorState", config,
    ) -> WorkerRunOutcome:
        spawned = spawn_worker(kind, input_context, state)
        if isinstance(spawned, SpawnError):
            return WorkerRunFailure(error=spawned)

        graph = self._subgraphs.get(kind)
        if graph is None:
            return WorkerRunFailure(
                error=SpawnError(reason=f"No compiled graph registered for workflow kind: {kind}"),
            )

        result_worker = await graph.ainvoke(spawned.arg, config=config)
        return WorkerRunSuccess(worker=result_worker)

    async def run_many(
        self,
        requests: Sequence[tuple[str, dict]],
        *,
        state: "OrchestratorState",
        config,
        max_concurrency: int = 4,
    ) -> list[WorkerRunOutcome]:
        """
        Future fan-out foundation (Parallelism Roadmap, Phase P1: "contract
        only"). Сохраняет порядок входа (asyncio.gather с порядком corutin),
        ограничивает конкурентность семафором.

        НЕ ИСПОЛЬЗОВАТЬ из production-оркестрации: нет объявленных
        reducer/merge-стратегий для workers/artifacts/messages, ownership-
        модели артефактов и serialised interrupt ownership (см. принцип 5 в
        roadmap и Phase P3 "Deterministic merge"). Годен только для
        тестов/эксперимента до тех пор, пока эти правила не появятся.
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run_one(kind: str, input_context: dict) -> WorkerRunOutcome:
            async with semaphore:
                return await self.run(kind, input_context, state, config)

        return list(await asyncio.gather(*(_run_one(kind, ctx) for kind, ctx in requests)))
