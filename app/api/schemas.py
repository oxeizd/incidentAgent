"""Контракты HTTP-слоя (единый вход + SSE). src/api/schemas.py

редактора: добавлен слой, приводящий внутренний interrupt-payload
(app/ai/graph/interrupts.py: type="question"|"confirmation"|"form") к общепризнанному
контракту вызова функций/инструментов — тому же, что используют
OpenAI Chat Completions/Responses API и совместимые с ним клиенты (Anthropic
tool_use, Vercel AI SDK и др.): ассистент не присылает голый вопрос, а просит
вызвать функцию с определённым именем и JSON-аргументами, клиент рендерит
по этому имени свой виджет (текст/да-нет/форма) и присылает результат.

Это ДОБАВОЧНый слой, не замена: старые поля (question/payload) остаются
как были, чтобы не сломать существующий фронтенд (app/api/static/index.html).
Новые поля (tool_calls) — для клиентов, которые уже умеют говорить на языке
OpenAI-совместимых function calls, включая генерацию форм по JSON Schema
аргументов.

ИСПРАВЛЕНО (composer-editor): раньше options допускал только string[],
а fields/тип полей не поддерживал textarea/select/placeholder/allowCustom/
submitLabel — новый composer-based UI (frontend/src/render/interactive.ts)
уже умеет всё это рендерить, здесь схема приведена в соответствие.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ── OpenAI-совместимый слой (function/tool calls) ─────────────────

_INTERRUPT_TYPE_TO_FUNCTION = {
    "question": "ask_user",
    "confirmation": "ask_confirmation",
    "form": "ask_form",
}

# Deprecated: старое имя словаря, оставлено на случай внешних импортов.
_INTERRUPT_KIND_TO_FUNCTION = _INTERRUPT_TYPE_TO_FUNCTION

_OPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "value": {"type": "string"},
        "allowCustom": {"type": "boolean"},
    },
    "required": ["label"],
}

_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "label": {"type": "string"},
        "required": {"type": "boolean"},
        "type": {
            "type": "string",
            "enum": ["string", "textarea", "boolean", "integer", "number", "array", "object", "select"],
        },
        "value": {"description": "Уже известное значение или null, если данных нет."},
        "placeholder": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "items": {
            "type": "object",
            "properties": {"type": {"type": "string"}},
            "required": ["type"],
        },
    },
    "required": ["name", "label", "required", "type"],
}

# Статический OpenAI tools=[...] контракт: клиент запрашивает его один раз
# (GET /api/v1/interrupt-tools) и знает полную схему аргументов заранее, а не
# выводит структуру по одному имени функции.
INTERRUPT_FUNCTION_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Задать пользователю вопрос: свободный текст или выбор варианта из списка.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": _OPTION_SCHEMA},
                    "submitLabel": {"type": "string"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_confirmation",
            "description": (
                "Запросить подтверждение да/нет. Обычные варианты должны нести "
                "value confirm/reject, чтобы клиент не угадывал смысл по label."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": _OPTION_SCHEMA},
                    "submitLabel": {"type": "string"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_form",
            "description": "Запросить заполнение структурированной формы из нескольких полей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "fields": {"type": "array", "items": _FIELD_SCHEMA},
                    "submitLabel": {"type": "string"},
                },
                "required": ["question", "fields"],
            },
        },
    },
]


class FunctionCall(BaseModel):
    """Как OpenAI ChatCompletionMessageToolCall.function: имя + JSON-строка аргументов."""
    name: str
    arguments: str = Field(description="JSON-строка с аргументами вызова (question, fields, options...)")


class ToolCall(BaseModel):
    """Как OpenAI ChatCompletionMessageToolCall — {id, type: 'function', function: {...}}."""
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    """
    Сообщение в OpenAI-совместимом формате (roles: system/user/assistant/tool).
    Пока не является обязательной частью входного контракта (см. MessageRequest
    ниже) — заведено для клиентов, которые хотят говорить с сервером на этом
    языке, и как база для будущего перехода API целиком на этот формат.
    """
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = Field(
        default=None, description="Обязательно для role='tool' — id вызова, на который отвечаем.",
    )


def interrupt_to_tool_call(interrupt_payload: dict[str, Any]) -> ToolCall:
    """
    Превращает внутренний interrupt-payload (worker_id, type, question,
    round, fields?, options?, submitLabel?) в OpenAI-совместимый tool_call.

    id вызова детерминирован (worker_id + round) — чтобы клиент, отвечая
    через role='tool', мог сослаться на tool_call_id и сервер мог сверить,
    что ответ относится к тому самому вопросу (полезно, когда в потоке
    несколько interrupt подряд — тот же принцип, что рекомендует LangGraph
    для маршрутизации нескольких interrupt по discriminator'у).
    """
    interrupt_type = interrupt_payload.get("type") or interrupt_payload.get("kind", "question")
    function_name = _INTERRUPT_TYPE_TO_FUNCTION.get(interrupt_type, "ask_user")
    worker_id = interrupt_payload.get("worker_id", "unknown")
    round_no = interrupt_payload.get("round", 0)

    arguments: dict[str, Any] = {"question": interrupt_payload.get("question", "")}
    for key in ("fields", "options", "submitLabel"):
        if key in interrupt_payload:
            arguments[key] = interrupt_payload[key]

    return ToolCall(
        id=f"call_{worker_id}_{round_no}",
        function=FunctionCall(name=function_name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


# ── Существующий унифицированный контракт (не трогаем ради обратной совместимости) ──

class MessageRequest(BaseModel):
    """Единый вход диалога: и новая реплика, и ответ на interrupt.

    Клиент не обязан знать, ждёт ли граф ввода — сервер сам смотрит чекпоинт
    треда и решает, что подать в граф: новое сообщение или Command(resume=...).
    """

    thread_id: str = Field(min_length=1, description="Идентификатор диалога")
    text: Optional[str] = Field(default=None, description="Текст реплики пользователя")
    payload: Optional[dict[str, Any]] = Field(
        default=None, description="Структурированный ответ на UI-блок (выбор/подтверждение/форма)"
    )
    tool_call_id: Optional[str] = Field(
        default=None,
        description=(
            "id tool_call'а (см. ToolCall выше), на который отвечает payload/text. "
            "Обязателен, когда тред ожидает ответа на interrupt (awaiting_input=True) — "
            "сервер строго сверяет его с текущим ожидаемым id и отвечает 409, если "
            "они не совпадают (устаревшая форма на клиенте)."
        ),
    )
    display_text: Optional[str] = Field(
        default=None,
        description=(
            "Человекочитаемый текст structured-ответа (payload) для истории чата — "
            "чтобы в ленте не откладывался сырой JSON.stringify(payload). Если не "
            "передан, сервер использует text, а для payload — сериализованный JSON."
        ),
    )


class ChatMessageRequest(BaseModel):
    """Упрощённый запрос для фронтенда: {message: "текст"}."""

    message: str = Field(description="Текст сообщения пользователя")


class ThreadStateResponse(BaseModel):
    thread_id: str
    awaiting_input: bool
    question: Optional[str] = None
    pending_artifact: Optional[dict[str, Any]] = None
    next_nodes: list[str] = Field(default_factory=list)
    current_artifact_id: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = Field(
        default=None,
        description=(
            "OpenAI-совместимое представление того же вопроса, что в 'question' — "
            "заполняется только когда awaiting_input=True. Ровно один элемент "
            "(параллельные interrupt'ы графом не предусмотрены)."
        ),
    )


class HealthResponse(BaseModel):
    status: str = "ok"
