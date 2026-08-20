from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.services.llm import llm_client


_CHAT_FALLBACK_PROMPT = """
Ты — единый ассистент по IT-инцидентам, RCA-справкам, поручениям и
презентациям.

Отвечай кратко и по-человечески. Ты можешь:
- найти инциденты и поручения;
- провести RCA по номеру, найденному инциденту или описанию;
- подготовить и отредактировать RCA-справку;
- собрать презентацию по инциденту или готовой справке.

Не утверждай, что выполнил поиск, RCA, сохранение или редактирование,
если для этого не был запущен соответствующий workflow.

Если запрос непонятен, предложи пользователю доступные действия.
"""


async def answer_chat(
    *,
    user_text: str,
) -> AIMessage:
    """
    Обычный диалог вне workflow.

    Он намеренно не изменяет active/suspended task и artifacts.
    """
    response = await llm_client.ainvoke(
        [
            llm_client.build_system_message(
                role_instruction=_CHAT_FALLBACK_PROMPT,
            ),
            HumanMessage(content=user_text),
        ],
        worker_kind="chat",
    )

    return AIMessage(
        content=str(response.content).strip()
    )