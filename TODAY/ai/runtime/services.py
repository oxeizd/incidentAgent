from __future__ import annotations

from app.memory.facade import MemoryFacade


_memory: MemoryFacade | None = None


def configure_runtime_services(
    *,
    memory: MemoryFacade,
) -> None:
    """
    Вызывается один раз на application startup.

    MemoryFacade — runtime dependency, а не LangGraph state. Его нельзя
    класть в worker input_context/payload, иначе checkpointer не сможет
    сериализовать state.
    """
    global _memory
    _memory = memory


def get_memory() -> MemoryFacade:
    memory = _memory

    if memory is None:
        raise RuntimeError(
            "AI runtime services are not configured. "
            "Call configure_runtime_services(memory=...) at startup."
        )

    return memory


def reset_runtime_services_for_tests() -> None:
    """
    Утилита для изолированных тестов.
    """

    global _memory
    _memory = None