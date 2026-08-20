from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.ai.schemas.conversation_state import ConversationState


def merge_state_updates(
    *updates: dict[str, Any],
) -> dict[str, Any]:
    """
    Склеивает последовательные state updates.

    Особый случай — messages: это append-only поле с reducer add_messages,
    поэтому сообщения не должны теряться при объединении lifecycle update,
    workflow update и user-facing status update.
    """
    merged: dict[str, Any] = {}
    merged_messages: list[AIMessage] = []

    for update in updates:
        for key, value in update.items():
            if key == "messages":
                merged_messages.extend(value)
            else:
                merged[key] = value

    if merged_messages:
        merged["messages"] = merged_messages

    return merged


def status_message(
    text: str,
) -> dict[str, Any]:
    """
    Публичный статус без раскрытия chain-of-thought.
    """
    return {
        "messages": [
            AIMessage(content=text)
        ],
        "last_status": text,
    }


def user_message(
    text: str,
) -> dict[str, Any]:
    """
    Обычное финальное или промежуточное сообщение ассистента.
    """
    return {
        "messages": [
            AIMessage(content=text)
        ]
    }