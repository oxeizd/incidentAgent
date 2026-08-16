
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from app.ai.schemas.worker import WorkerState
from app.ai.nodes.search import resolve_node, search_node
from app.ai.nodes.rca import rca_gate, analyzer, task_validator, route_after_gate
from app.ai.nodes.editor import apply_edit
from app.ai.nodes.creator import collect_fields, build_presentation, route_after_collect

_STOP_STATUSES = ("failed", "deviated")


def build_search_graph():
    g = StateGraph(WorkerState)
    g.add_node("resolve_entity", resolve_node)
    g.add_node("search", search_node)
    g.add_edge(START, "resolve_entity")
    g.add_conditional_edges(
        "resolve_entity",
        lambda w: END if w["status"] in _STOP_STATUSES else "search",
        {"search": "search", END: END},
    )
    g.add_edge("search", END)
    return g.compile()


def build_rca_graph():
    g = StateGraph(WorkerState)
    g.add_node("rca_gate", rca_gate)
    g.add_node("analyzer", analyzer)
    g.add_node("task_validator", task_validator)
    g.add_edge(START, "rca_gate")
    g.add_conditional_edges(
        "rca_gate", route_after_gate,
        {"rca_gate": "rca_gate", "analyzer": "analyzer", "END": END},
    )
    g.add_edge("analyzer", "task_validator")
    g.add_edge("task_validator", END)
    return g.compile()


def build_editor_graph():
    g = StateGraph(WorkerState)
    g.add_node("apply_edit", apply_edit)
    g.add_edge(START, "apply_edit")
    g.add_edge("apply_edit", END)
    return g.compile()


def build_creator_graph():
    g = StateGraph(WorkerState)
    g.add_node("collect_fields", collect_fields)
    g.add_node("build_presentation", build_presentation)
    g.add_edge(START, "collect_fields")
    g.add_conditional_edges(
        "collect_fields", route_after_collect,
        {"collect_fields": "collect_fields", "build_presentation": "build_presentation", "END": END},
    )
    g.add_edge("build_presentation", END)
    return g.compile()


def build_subgraphs() -> dict:
    return {
        "search": build_search_graph(),
        "rca": build_rca_graph(),
        "editor": build_editor_graph(),
        "creator": build_creator_graph(),
    }