from __future__ import annotations

from langchain_core.tools import BaseTool


TOOL_REGISTRY: dict[str, list[BaseTool]] = {}


def register_toolset(
    role: str,
    tools: list[BaseTool],
    *,
    overwrite: bool = False,
) -> None:
    """
    Регистрирует tools worker role.

    Повторная регистрация с теми же именами является no-op. Это позволяет
    безопасно вызывать bootstrap в test lifespan и при hot reload.
    """
    tool_names = [tool.name for tool in tools]

    if len(tool_names) != len(set(tool_names)):
        raise ValueError(
            f"Toolset for role {role!r} contains duplicate tool names"
        )

    existing = TOOL_REGISTRY.get(role)

    if existing is not None:
        existing_names = [tool.name for tool in existing]

        if existing_names == tool_names:
            return

        if not overwrite:
            raise ValueError(
                f"Toolset for role {role!r} is already registered "
                "with a different tool list"
            )

    TOOL_REGISTRY[role] = list(tools)


def get_tools(
    role: str,
) -> list[BaseTool]:
    """
    Возвращает copy, чтобы node/tool loop не мог изменить registry.
    """
    return list(TOOL_REGISTRY.get(role, []))


def clear_tool_registry() -> None:
    """
    Только для изолированных тестов.
    """
    TOOL_REGISTRY.clear()