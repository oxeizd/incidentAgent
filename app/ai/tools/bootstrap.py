"""Built-in toolset bootstrap; repeat-safe through register_toolset()."""
from __future__ import annotations

from app.ai.tools.registry import register_toolset
from app.ai.tools.resolve_tools import ask_user_clarify, lookup_entity
from app.ai.tools.search_tools import search_assignments_tool, search_incidents_tool


def register_builtin_toolsets() -> None:
    register_toolset("search", [search_incidents_tool, search_assignments_tool])
    register_toolset("resolve_entity", [lookup_entity, ask_user_clarify])
