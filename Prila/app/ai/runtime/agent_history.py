from __future__ import annotations

from typing import Any

from app.ai.schemas.conversation import (
    AgentHistory,
    AgentHistoryEvent,
    utc_now_iso,
)


def load_agent_history(
    data: dict[str, Any],
    *,
    agent: str,
    max_events: int = 24,
) -> AgentHistory:
    """
    Возвращает локальную историю агента из snapshot.data.

    Если history ещё не создана или schema изменилась, создаётся пустая
    bounded history. Это удобно для первого запуска task и миграций.
    """
    raw = (data.get("agent_history") or {}).get(agent)

    if raw is None:
        return AgentHistory(
            agent=agent,
            max_events=max_events,
        )

    return AgentHistory.model_validate(raw)


def append_agent_event(
    data: dict[str, Any],
    *,
    agent: str,
    role: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    max_events: int = 24,
) -> dict[str, Any]:
    """
    Возвращает новый snapshot.data с добавленным bounded event.

    Не мутирует входной dict. При параллельных агентах каждый пишет в свой
    ключ agent_history[agent], поэтому Search Normalizer, executor, RCA
    gate и editor не смешивают свои рабочие транскрипты.
    """
    history = load_agent_history(
        data,
        agent=agent,
        max_events=max_events,
    )

    updated = history.append(
        AgentHistoryEvent(
            role=role,
            kind=kind,
            payload=payload or {},
            created_at=utc_now_iso(),
        )
    )

    histories = dict(data.get("agent_history") or {})
    histories[agent] = updated.model_dump(mode="json")

    return {
        **data,
        "agent_history": histories,
    }


def get_agent_events(
    data: dict[str, Any],
    *,
    agent: str,
) -> list[dict[str, Any]]:
    """
    Возвращает JSON-safe события для формирования LLM context.

    Конкретный workflow сам решает, какие события включить в prompt:
    например Search Normalizer использует query, lookup and selection,
    RCA gate — evidence и ответы на вопросы.
    """
    history = load_agent_history(data, agent=agent)

    return [
        event.model_dump(mode="json")
        for event in history.events
    ]