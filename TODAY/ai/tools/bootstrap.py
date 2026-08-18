from __future__ import annotations

from app.ai.tools.registry import register_toolset
from app.ai.tools.resolve_tools import lookup_entity
from app.ai.tools.search_tools import (
    find_similar_assignments_for_agent,
    find_similar_incidents_for_agent,
    retrieve_assignments_for_agent,
    retrieve_incidents_for_agent,
)


def register_builtin_toolsets() -> None:
    """
    Idempotent built-in tools bootstrap.

    Resolver не получает interrupt-tool: любое user question контролирует
    resolve_entity node через Pydantic ResolverDecision + ctx.ask().
    """
    register_toolset(
        "resolve_entity",
        [
            lookup_entity,
        ],
    )

    register_toolset(
        "search",
        [
            retrieve_incidents_for_agent,
            retrieve_assignments_for_agent,
            find_similar_incidents_for_agent,
            find_similar_assignments_for_agent,
        ],
    )