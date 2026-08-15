"""
Контракт для клиента (единственное, что нужно знать):
- event: "message"  -> {content: str, artifact: dict|None, awaiting_input: bool, tool_calls: list|None}
                        awaiting_input=True  -> это вопрос, ждём ответ пользователя
                        awaiting_input=False -> это финальный ответ, диалог свободен
                        tool_calls           -> заполнено только при awaiting_input=True;
                                                 OpenAI-совместимое представление того
                                                 вопроса (см. app/api/schemas.py:interrupt_to_tool_call) —
                                                 клиенты, понимающие function/tool calls, могут
                                                 рендерить форму/да-нет-виджет по tool_calls[0].function,
                                                 не разбирая content как текст.
- event: "error"    -> {message: str}
- event: "done"     -> {ok: bool}  (чисто технический маркер конца потока)

"reasoning" по-прежнему эмитится (для тех клиентов, что хотят прогресс-индикатор),
но НЕ является частью обязательного контракта — можно игнорировать это событие
целиком и не потерять ничего важного.
"""

from __future__ import annotations
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.api.schemas import interrupt_to_tool_call

logger = logging.getLogger(__name__)


def sse_event(event: str, data: Any) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}


async def stream_graph_events(graph, graph_input, config, *, suppress: bool = False) -> AsyncIterator[dict[str, str]]:
    """
    Возвращает поток SSE. ВАЖНО для вызывающего кода (app.py): последнее
    событие "message" в потоке — это то, что нужно сохранить в постоянную
    историю чата (и как ответ пользователю, и как вопрос — они неразличимы
    для клиента, различаются только полем awaiting_input).
    """
    interrupted = False
    final_message: str | None = None
    final_artifact: dict[str, Any] | None = None
    final_tool_calls: list[dict[str, Any]] | None = None

    try:
        async for namespace, mode, chunk in graph.astream(
            graph_input, config=config, stream_mode=["updates", "custom"], subgraphs=True,
        ):
            if mode == "custom":
                # Необязательный прогресс-индикатор — не часть обязательного контракта.
                yield sse_event("reasoning", chunk)
                continue

            for node_name, update in (chunk or {}).items():
                if node_name == "__interrupt__":
                    if interrupted or suppress:
                        continue
                    interrupted = True
                    interrupt_payload = _interrupt_payload(update)
                    # ИСПРАВЛЕНО: вопрос агента идёт под тем же event="message",
                    # что и финальный ответ — клиенту не нужно различать их природу.
                    final_message = interrupt_payload.get("question")
                    final_artifact = None
                    final_tool_calls = [interrupt_to_tool_call(interrupt_payload).model_dump()]
                    continue

                if not isinstance(update, dict):
                    continue

                artifacts = update.get("artifacts")
                current_artifact_id = update.get("current_artifact_id")
                if artifacts and current_artifact_id and current_artifact_id in artifacts:
                    final_artifact = artifacts[current_artifact_id]

                messages = update.get("messages")
                if messages:
                    last = messages[-1] if isinstance(messages, list) else messages
                    content = getattr(last, "content", None)
                    if content:
                        final_message = content
    except Exception as exc:
        logger.exception("Ошибка потоковой обработки графа")
        yield sse_event("error", {"message": "Внутренняя ошибка обработки запроса"})
        yield sse_event("done", {"ok": False})
        return

    if final_message:
        yield sse_event("message", {
            "content": final_message,
            "artifact": final_artifact,
            "awaiting_input": interrupted,
            "tool_calls": final_tool_calls if interrupted else None,
        })

    yield sse_event("done", {"ok": True})


def _interrupt_payload(update: Any) -> dict[str, Any]:
    items = update if isinstance(update, (list, tuple)) else [update]
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            return value
    # ИСПРАВЛЕНО: фоллбек-конверт не содержал "type" — единственное место,
    # где дискриминатор ещё назывался только "kind". Теперь оба поля, как
    # и во всех остальных interrupt-payload'ах (см. app/ai/graph/interrupts.py).
    return {"question": "Уточните запрос", "type": "question", "kind": "question"}
