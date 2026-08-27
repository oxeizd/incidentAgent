from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from app.ai.runtime.agent_messages import to_llm_messages
from app.ai.runtime.tool_loop import ToolLoopError, ToolLoopRunner
from app.ai.schemas.conversation import AgentConversation
from app.ai.workflows.search.tools import lookup_entities
from app.services.llm import llm_client


MAX_TOOL_ROUNDS = 4

tool_loop_runner = ToolLoopRunner(max_rounds=MAX_TOOL_ROUNDS)


SEARCH_RESOLVER_PROMPT = """
Ты — Resolver поиска IT-ассистента.

Твоя задача:
1. Понять, какие catalog entities явно назвал пользователь.
2. Для каждого такого объекта вызвать lookup_entities.
3. По результатам tool calls выдать ОДИН обычный нормализованный текст для
   внутреннего Search Agent.

Ты не возвращаешь JSON. Ты не создаёшь SQL. Ты не показываешь пользователю
внутренние рассуждения и не упоминаешь tools.

Когда вызывать lookup_entities:
- system_name: система, услуга, сервис, продукт, платформа, интеграция,
  контур, CRM, источник данных, «внешние данные»;
- work_group: команда, группа поддержки, подразделение;
- executor_name: конкретный исполнитель или сотрудник;
- element_name: сервер, БД, хост, endpoint, очередь, конфигурационный
  элемент.

Если пользователь говорит «на/по/в <система или сервис>», обязательно вызови
system_name lookup.

Не вызывай lookup_entities для дат, периода, номеров инцидентов/поручений,
статусов, приоритетов, метрик, ошибок и свободного текста.

После tool calls:
- если status=matched, используй только canonical value из match.value;
- если status=ambiguous, укажи, что объект неоднозначен, и перечисли
  candidates;
- если status=not_found, сохрани исходную phrase и укажи, что canonical name
  не найден;
- сохрани пользовательский смысл и все ограничения.

Нормализуй даты по current_datetime_utc:
- «май» означает первый и последний момент мая текущего года;
- конец календарного дня включителен;
- не придумывай период, которого нет в пользовательской переписке.

Пример:
Пользователь: «найди внешние данные за май».
После matched lookup:
«Найти инциденты по system_name: ППРБ.КМ.Внешние данные (CI04645949);
период: 2026-05-01T00:00:00Z .. 2026-05-31T23:59:59Z.»

Ответь только нормализованным запросом для Search Agent.
"""


class SearchResolverError(RuntimeError):
    """Resolver не смог сформировать пригодный нормализованный запрос."""


async def resolve_search_request(
    *,
    conversation: AgentConversation,
) -> str:
    """
    Нормализует локальную пользовательскую переписку search-шага.

    Tool calls и результат resolver-а существуют только в памяти текущего
    запуска. В StepRun.conversation ничего техническое не записывается:
    там остаются только реальные user/assistant реплики search-агента.
    """
    history_messages = to_llm_messages(conversation)
    if not history_messages:
        raise SearchResolverError("Search conversation is empty.")

    system_message = llm_client.build_system_message(
        role_instruction=SEARCH_RESOLVER_PROMPT,
        extra_context={
            "current_datetime_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    messages: list[BaseMessage] = [
        system_message,
        *history_messages,
        HumanMessage(
            content=(
                "Сформируй нормализованный запрос для Search Agent на основе "
                "всей переписки выше."
            )
        ),
    ]

    try:
        result = await tool_loop_runner.run(
            messages,
            tools=[lookup_entities],
            worker_kind="search_resolver",
        )
    except ToolLoopError as exc:
        raise SearchResolverError(
            "Search resolver did not finish tool protocol."
        ) from exc
    except Exception as exc:
        raise SearchResolverError(
            "Search resolver invocation failed."
        ) from exc

    return _extract_text(result.message.content)


def _extract_text(content: Any) -> str:
    if not isinstance(content, str):
        raise SearchResolverError("Resolver returned non-text response.")

    text = content.strip()
    if not text:
        raise SearchResolverError("Resolver returned empty normalized request.")

    if len(text) > 10_000:
        raise SearchResolverError("Resolver response exceeds limit.")

    return text