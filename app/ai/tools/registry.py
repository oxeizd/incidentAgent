"""
app/ai/tools/registry.py

Wave 2 (idempotent bootstrap):
- register_toolset() с ТЕМ ЖЕ (по значению — списки тулов равны поэлементно)
  списком под тем же role — no-op вместо ValueError. Разный список под тем
  же role без overwrite=True — по-прежнему ValueError.
- get_tools() возвращает КОПИЮ списка — вызывающий код не может случайно
  замутировать глобальный реестр (append/remove тула из чужого списка,
  полученного через get_tools(), больше не виден другим потребителям).
"""
from __future__ import annotations

TOOL_REGISTRY: dict[str, list] = {}


def register_toolset(role: str, tools: list, *, overwrite: bool = False) -> None:
    existing = TOOL_REGISTRY.get(role)
    if existing is not None and not overwrite:
        if list(existing) == list(tools):
            return
        raise ValueError(f"toolset for role '{role}' already registered with a different tool list")
    TOOL_REGISTRY[role] = list(tools)


def get_tools(role: str) -> list:
    return list(TOOL_REGISTRY.get(role, []))
