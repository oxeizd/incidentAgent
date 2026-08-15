from __future__ import annotations

TOOL_REGISTRY: dict[str, list] = {}


def register_toolset(role: str, tools: list, *, overwrite: bool = False) -> None:
    if role in TOOL_REGISTRY and not overwrite:
        raise ValueError(f"toolset for role '{role}' already registered")
    TOOL_REGISTRY[role] = tools


def get_tools(role: str) -> list:
    return TOOL_REGISTRY.get(role, [])