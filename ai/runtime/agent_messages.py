
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.schemas.conversation import AgentConversation


def to_llm_messages(
    conversation: AgentConversation,
) -> list[HumanMessage | AIMessage]:
    """
    Конвертирует локальную историю одного шага в обычные LLM messages.

    System prompt строится отдельно и добавляется вызывающим workflow первым.
    Эта функция передаёт только рабочую переписку конкретного search/RCA/
    presentation шага: user -> HumanMessage, assistant -> AIMessage.
    """
    messages: list[HumanMessage | AIMessage] = []

    for message in conversation.messages:
        if message.role == "user":
            messages.append(HumanMessage(content=message.content))
        else:
            messages.append(AIMessage(content=message.content))

    return messages