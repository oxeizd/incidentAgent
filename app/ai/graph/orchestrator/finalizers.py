"""
app/ai/graph/orchestrator/finalizers.py

Крошечный dict-based dispatch "какую finalize-функцию из app/ai/graph/merge.py
применить для этого worker kind" — то же, что раньше было словарём
_FINALIZERS прямо внутри orchestrator.py. Вынесено в отдельный модуль, чтобы
handlers импортировали его напрямую и не тянули за собой весь orchestrator.
"""
from __future__ import annotations
from typing import Callable

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState
from app.ai.graph.merge import finalize_worker, finalize_editor_result

FinalizeFn = Callable[[OrchestratorState, WorkerState], dict]

_FINALIZERS: dict[str, FinalizeFn] = {
    "editor": finalize_editor_result,
}


def finalize_for(kind: str) -> FinalizeFn:
    return _FINALIZERS.get(kind, finalize_worker)
