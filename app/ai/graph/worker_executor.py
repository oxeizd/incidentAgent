"""
app/ai/graph/worker_executor.py — transition facade над WorkerRunner.

Wave 3: подграфы (search/rca/editor/creator) собираются один раз и кэшируются
здесь — тот же принцип, что раньше жил в модульной переменной
`_subgraphs_cache` внутри app/ai/graph/orchestrator.py (чистая структурная
сборка без per-request состояния, пересобирать на каждой реплике каждого
треда не нужно).

`spawn_and_run()` — совместимый по сигнатуре аналог старого
orchestrator._spawn_and_run(): возвращает (worker_dict_or_None, SpawnError_or_None),
чтобы intent handlers (app/ai/graph/orchestrator/handlers/*) переносились из
старого orchestrator.py построчно, без немедленной переделки под
WorkerRunSuccess/WorkerRunFailure. Новый код, которому нужен typed outcome
напрямую, может использовать get_worker_runner().run(...) вместо этой обёртки.
"""
from __future__ import annotations

from typing import Optional

from app.ai.graph.build import build_subgraphs
from app.ai.runtime.worker_runner import WorkerRunner, WorkerRunFailure, WorkerRunSuccess
from app.ai.runtime.factory import SpawnError

_runner: Optional[WorkerRunner] = None


def get_worker_runner() -> WorkerRunner:
    global _runner
    if _runner is None:
        _runner = WorkerRunner(build_subgraphs())
    return _runner


async def spawn_and_run(kind: str, input_context: dict, state, config):
    outcome = await get_worker_runner().run(kind, input_context, state, config)
    if isinstance(outcome, WorkerRunFailure):
        return None, outcome.error
    assert isinstance(outcome, WorkerRunSuccess)
    return outcome.worker, None
