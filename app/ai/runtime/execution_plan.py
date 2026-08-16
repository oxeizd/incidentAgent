"""
app/ai/runtime/execution_plan.py

Parallelism Roadmap, Phase P2: contract only. This module deliberately does
NOT execute plans. WorkerRunner.run_many() remains unused by production
orchestration until deterministic reducers, artifact ownership, message order,
interrupt ownership, cancellation and partial-failure rules are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerRequest:
    kind: str
    input_context: dict[str, Any]
    merge_key: str
    independent: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    sequential: tuple[WorkerRequest, ...] = ()
    parallel_groups: tuple[tuple[WorkerRequest, ...], ...] = ()

    def validate_contract(self) -> None:
        for group in self.parallel_groups:
            if not group:
                raise ValueError("parallel group must not be empty")
            if any(not request.independent for request in group):
                raise ValueError("every request in a parallel group must explicitly declare independent=True")
            merge_keys = [request.merge_key for request in group]
            if len(merge_keys) != len(set(merge_keys)):
                raise ValueError("parallel group requires unique merge_key values")
