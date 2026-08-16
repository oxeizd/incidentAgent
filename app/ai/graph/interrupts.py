"""
app/ai/graph/interrupts.py

Единый конверт для всех вопросов агента к пользователю (LangGraph
interrupt() payload). "type" — discriminator, по которому клиент выбирает
виджет рендера — та же конвенция, что и везде в остальном контракте этого
проекта (app/services/reasoning.py: {"type": "reasoning", ...},
app/services/llm.py: response_format={"type": "json_schema", ...},
app/api/schemas.py: ToolCall.type), и та же, что использует индустрия для
дискриминаторов JSON-конвертов: OpenAPI/JSON Schema discriminator,
CloudEvents "type", Stripe webhook "type", AG-UI BaseEvent.type (см.
https://docs.ag-ui.com/sdk/js/core/events — "type: EventType // Discriminator
field").

ИСПРАВЛЕНО: раньше здесь было поле "kind" — единственное место во всём
контракте, где дискриминатор назывался иначе, чем везде в проекте (то есть
несоответствие не только внешним стандартам, но и уже принятой в этом же
коде конвенции). Переименовано в "type". Поле "kind" оставлено ДОПОЛНИТЕЛЬНО
(дублирует то же значение) как deprecated-алиас — старые клиенты/уже
записанные в БД сообщения, читавшие "kind" из показанных ранее вопросов, не
ломаются; новый код должен читать "type".

Поддерживаемые type:
  - "question"      — свободный текстовый вопрос, ответ = строка.
  - "confirmation"  — да/нет, ответ = bool | {"confirmed": bool} | "да"/"нет".
  - "form"          — структурированная форма из нескольких полей (см.
                      app/ai/runtime/form_schema.py), ответ = dict {field: value}
                      ИЛИ свободный текст (клиент без поддержки форм) —
                      обрабатывающая нода обязана понимать оба варианта.
"""
from __future__ import annotations
from typing import Literal

InterruptType = Literal["question", "confirmation", "form"]

# Deprecated: старое имя алиаса типа. Оставлено только чтобы не сломать
# случайный `from app.ai.graph.interrupts import InterruptKind`, если он
# где-то есть за пределами этого пакета. Новый код должен использовать
# InterruptType.
InterruptKind = InterruptType


def ask_user(question: str, worker_id: str, *, type: InterruptType = "question", **extra) -> dict:
    """
    type — discriminator (см. докстринг модуля). Параметр назван `type`
    сознательно, даже несмотря на совпадение с встроенным именем Python —
    любое другое имя параметра расходилось бы с полем, которое реально
    видит клиент в JSON, и создавало бы новое несоответствие вроде того,
    которое здесь правится.

    "kind" в возвращаемом словаре — deprecated-дубликат того же значения,
    см. докстринг модуля. Убрать после того, как все клиенты перейдут на
    "type" (не раньше отдельного релиза с явным объявлением об удалении).
    """
    return {"question": question, "worker_id": worker_id, "type": type, "kind": type, **extra}
