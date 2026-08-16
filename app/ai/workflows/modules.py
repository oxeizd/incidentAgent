"""
app/ai/workflows/modules.py

Wave 8 foundation. WorkflowModule colocates immutable workflow metadata with
an explicit graph builder. Existing build.py may remain during transition;
bootstrap can progressively consume these modules after tests pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.ai.registry.workflows import RegisteredWorkflow


@dataclass(frozen=True)
class WorkflowModule:
    spec: RegisteredWorkflow
    build_graph: Callable[[], Any]
