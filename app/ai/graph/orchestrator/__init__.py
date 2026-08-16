"""
Compatibility facade for app.ai.graph.orchestrator.

IMPORTANT MIGRATION NOTE
------------------------
Python cannot safely host BOTH:
  app/ai/graph/orchestrator.py
  app/ai/graph/orchestrator/__init__.py
in the same package directory: a module and a package with the same import
name conflict. Complete the switch atomically:

1. Replace/remove the legacy file app/ai/graph/orchestrator.py.
2. Create directory app/ai/graph/orchestrator/.
3. Put this file into app/ai/graph/orchestrator/__init__.py.
4. Put the extracted modules into that directory.

Existing callers remain compatible:
    from app.ai.graph.orchestrator import build_orchestrator_graph
    from app.ai.graph.orchestrator import classify_intent, run_worker
"""
from __future__ import annotations

from app.ai.graph.orchestrator.classifier import classify_intent
from app.ai.graph.orchestrator.graph import (
    build_orchestrator_graph,
    dispatch_intent,
    get_orchestrator_context,
    run_worker,
)
from app.ai.graph.orchestrator.models import EditRequest, IntentClassification, RoutingDecision

__all__ = [
    "EditRequest",
    "IntentClassification",
    "RoutingDecision",
    "build_orchestrator_graph",
    "classify_intent",
    "dispatch_intent",
    "get_orchestrator_context",
    "run_worker",
]
