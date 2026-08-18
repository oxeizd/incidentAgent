from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.ai.nodes.creator import (
    build_presentation,
    collect_fields,
    route_after_collect,
)
from app.ai.nodes.editor import apply_edit
from app.ai.nodes.rca import (
    analyzer,
    rca_gate,
    route_after_gate,
    task_validator,
)
from app.ai.nodes.search import resolve_entity, search
from app.ai.schemas.worker import WorkerState


_STOP_STATUSES = frozenset(
    {
        "failed",
        "cancelled",
        "deviated",
    }
)


def route_after_resolve(
    worker: WorkerState,
) -> str:
    if worker["status"] in _STOP_STATUSES:
        return "END"

    return "search"


def build_search_graph():
    graph = StateGraph(WorkerState)

    graph.add_node("resolve_entity", resolve_entity)
    graph.add_node("search", search)

    graph.add_edge(START, "resolve_entity")

    graph.add_conditional_edges(
        "resolve_entity",
        route_after_resolve,
        {
            "search": "search",
            "END": END,
        },
    )

    graph.add_edge("search", END)

    return graph.compile()


def build_rca_graph():
    graph = StateGraph(WorkerState)

    graph.add_node("rca_gate", rca_gate)
    graph.add_node("analyzer", analyzer)
    graph.add_node("task_validator", task_validator)

    graph.add_edge(START, "rca_gate")

    graph.add_conditional_edges(
        "rca_gate",
        route_after_gate,
        {
            "rca_gate": "rca_gate",
            "analyzer": "analyzer",
            "END": END,
        },
    )

    graph.add_edge("analyzer", "task_validator")
    graph.add_edge("task_validator", END)

    return graph.compile()


def build_editor_graph():
    graph = StateGraph(WorkerState)

    graph.add_node("apply_edit", apply_edit)

    graph.add_edge(START, "apply_edit")
    graph.add_edge("apply_edit", END)

    return graph.compile()


def build_creator_graph():
    """
    Creator subgraph:

        START
          ↓
        collect_fields
          ├─ missing fields → collect_fields after interrupt/form response
          ├─ all required fields → build_presentation
          └─ failed/cancelled/deviated → END
          ↓
        build_presentation
          ↓
        END
    """
    graph = StateGraph(WorkerState)

    graph.add_node("collect_fields", collect_fields)
    graph.add_node("build_presentation", build_presentation)

    graph.add_edge(START, "collect_fields")

    graph.add_conditional_edges(
        "collect_fields",
        route_after_collect,
        {
            "collect_fields": "collect_fields",
            "build_presentation": "build_presentation",
            "END": END,
        },
    )

    graph.add_edge("build_presentation", END)

    return graph.compile()


def build_subgraphs() -> dict:
    return {
        "search": build_search_graph(),
        "rca": build_rca_graph(),
        "editor": build_editor_graph(),
        "creator": build_creator_graph(),
    }