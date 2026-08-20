from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import ValidationError

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.search.contracts import (
    CatalogCandidate,
    SearchNormalizationDecision,
)
from app.ai.workflows.search.tools import lookup_entity
from app.services.llm import llm_client


logger = logging.getLogger(__name__)


_AGENT_NAME = "search_normalizer"
_HISTORY_LIMIT = 30
_MAX_TOOL_ROUNDS = 5
_MAX_DECISION_RETRIES = 2


_NORMALIZER_PROMPT = """
Ты — агент нормализации поиска по IT-инцидентам и поручениям.

Ты получаешь текущий запрос пользователя и локальную историю этой задачи.
Твоя цель: либо подготовить точный план поиска, либо определить, какой
вопрос нужно задать пользователю, если поиск нельзя безопасно продолжить.

У тебя есть инструмент lookup_entity(raw_value). Он ищет по единому
каталогу и возвращает реальные candidates разных типов:
- system_name;
- work_group;
- executor_name;
- element_name.

Используй lookup_entity, когда в запросе есть название системы, сервиса,
рабочей группы, человека или компонента, которое требуется нормализовать.
Ты можешь вызвать tool несколько раз в рамках одного запроса — по одному
разу для каждой независимой сущности.

Не вызывай tool для номера инцидента, дат/периодов, статуса, приоритета,
числовых метрик и свободного описания проблемы.

После tool result:
- оцени candidates только в контексте всей задачи;
- не выбирай произвольно между равнозначными вариантами;
- если нужно выбрать один из нескольких реальных кандидатов, верни
  action="select_candidate", понятный question и option_ids только из
  фактически полученных candidate.id;
- если нужный термин не найден, период неясен, фильтры противоречат друг
  другу или не хватает другого критичного условия, верни action="clarify"
  с одним конкретным question без option_ids;
- если данных достаточно, верни action="execute" с SearchPlan;
- в structured filters используй canonical name из tool result, не raw text;
- used_candidate_ids должен содержать id всех каталожных candidates,
  чьи canonical names вошли в filters.

Выбор SearchPlan:
- entity="incidents" для поиска инцидентов;
- entity="assignments" для поиска поручений/мероприятий;
- mode="structured" для номера, статуса, приоритета, каталожной сущности,
  дат, периода или числовой метрики;
- mode="semantic_similarity" для похожих случаев либо свободного описания
  без точных фильтров;
- для semantic mode используй самостоятельный query_text без слов
  «найди», «покажи», «похожие».

Допустимые structured filters incidents:
number, status, priority_code, system_name, work_group, element_name,
executor_name, stand, start_time_from, start_time_to, end_time_from,
end_time_to, mttd_min, mttd_max, mttr_min, mttr_max, downtime_min,
downtime_max.

Допустимые structured filters assignments:
id, incident_id, ior, unit, responsible, status, deadline_from,
deadline_to, assigned_at_from, assigned_at_to.

Если запрос недостаточно ясен, не подменяй его произвольным поиском.

- Используй action="select_candidate" только если у тебя есть минимум два
  реальных candidate.id, между которыми пользователь должен выбрать.
- Используй action="clarify", если вариантов выбора нет, но нужно уточнение
  по термину, периоду, типу объекта, конфликтующим фильтрам или цели поиска.
- Не используй action="clarify", когда уже есть равнозначные кандидаты:
  в этом случае обязательно покажи их через action="select_candidate".
- Не придумывай option_ids, canonical names, incident IDs, даты или
  результаты поиска.

Финальный ответ, когда ты закончил tool calls, — только JSON строго по
схеме SearchNormalizationDecision. Без Markdown и текста до/после.
"""


@dataclass(frozen=True, slots=True)
class NormalizerOutcome:
    decision: SearchNormalizationDecision
    snapshot_data: dict[str, Any]


class SearchNormalizerError(RuntimeError):
    """
    Контролируемая ошибка normalizer-а.

    Workflow превращает её в понятное пользовательское сообщение и оставляет
    task доступной для повторной попытки, если это уместно.
    """


async def run_normalizer(
    *,
    snapshot: TaskSnapshot,
    user_text: str,
) -> NormalizerOutcome:
    """
    Выполняет одну нормализационную итерацию.

    История в snapshot.data уже содержит предыдущие tool calls, decisions,
    вопросы и ответы. Новый user_text добавляется как history event, затем
    каждый текущий tool result добавляется туда же.

    Возвращает новый JSON-safe snapshot_data, который workflow обязан
    сохранить через advance_stage/set_awaiting_input.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_AGENT_NAME,
        role="user",
        kind="input",
        payload={"text": user_text},
        max_events=_HISTORY_LIMIT,
    )

    conversation = _build_conversation(
        events=get_agent_events(
            data,
            agent=_AGENT_NAME,
        )
    )

    llm = llm_client.bind_tools(
        [lookup_entity],
        worker_kind="search_normalizer",
    )

    final_text: str | None = None

    for _ in range(_MAX_TOOL_ROUNDS):
        response = await llm.ainvoke(conversation)
        conversation.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            content = getattr(response, "content", None)

            if isinstance(content, str) and content.strip():
                final_text = content.strip()

            break

        for raw_call in tool_calls:
            tool_call_id, raw_value = _parse_tool_call(raw_call)

            if raw_value is None:
                tool_payload = {
                    "raw_value": None,
                    "candidates": [],
                    "error": (
                        "raw_value должен быть непустой строкой."
                    ),
                }
            else:
                try:
                    raw_result = await lookup_entity.ainvoke(
                        {"raw_value": raw_value}
                    )
                    candidates = _parse_candidates(raw_result)

                    tool_payload = {
                        "raw_value": raw_value,
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in candidates
                        ],
                    }
                except Exception:
                    logger.exception(
                        "lookup_entity failed: raw_value=%r",
                        raw_value,
                    )

                    tool_payload = {
                        "raw_value": raw_value,
                        "candidates": [],
                        "error": "lookup временно недоступен.",
                    }

            data = append_agent_event(
                data,
                agent=_AGENT_NAME,
                role="tool",
                kind="lookup_entity",
                payload=tool_payload,
                max_events=_HISTORY_LIMIT,
            )

            conversation.append(
                ToolMessage(
                    tool_call_id=tool_call_id,
                    content=json.dumps(
                        tool_payload,
                        ensure_ascii=False,
                    ),
                )
            )

    if final_text is None:
        raise SearchNormalizerError(
            "Не удалось завершить нормализацию поискового запроса."
        )

    decision, data = await _parse_and_validate_decision(
        final_text=final_text,
        conversation=conversation,
        snapshot_data=data,
    )

    return NormalizerOutcome(
        decision=decision,
        snapshot_data=data,
    )


def _build_conversation(
    *,
    events: list[dict[str, Any]],
) -> list[BaseMessage]:
    """
    Восстанавливает LLM context из сериализуемой локальной истории.

    Мы не восстанавливаем старые tool protocol messages 1:1. Вместо этого
    передаём history как компактный verified context, а текущие tool calls
    внутри run_normalizer всё равно идут нативно через ToolMessage.
    """
    history_json = json.dumps(
        events,
        ensure_ascii=False,
    )

    system = llm_client.build_system_message(
        role_instruction=_NORMALIZER_PROMPT,
        extra_context={
            "verified_task_history": history_json,
        },
        output_contract=(
            "После завершения tool calls верни JSON строго по схеме "
            "SearchNormalizationDecision."
        ),
    )

    latest_user_text = ""

    for event in reversed(events):
        if event["role"] == "user":
            latest_user_text = str(
                event.get("payload", {}).get("text", "")
            ).strip()
            break

    return [
        system,
        HumanMessage(content=latest_user_text),
    ]


async def _parse_and_validate_decision(
    *,
    final_text: str,
    conversation: list[BaseMessage],
    snapshot_data: dict[str, Any],
) -> tuple[SearchNormalizationDecision, dict[str, Any]]:
    """
    Валидирует decision и при ошибке просит LLM исправить только JSON.

    Проверка candidate IDs опирается на tool results из local history:
    модель может использовать/показывать только реально возвращённые
    каталогом кандидаты.
    """
    data = snapshot_data
    attempt_text = final_text

    for attempt in range(_MAX_DECISION_RETRIES + 1):
        try:
            decision = SearchNormalizationDecision.model_validate_json(
                attempt_text
            )

            _validate_referenced_candidates(
                decision=decision,
                snapshot_data=data,
            )

            data = append_agent_event(
                data,
                agent=_AGENT_NAME,
                role="assistant",
                kind="normalization_decision",
                payload=decision.model_dump(mode="json"),
                max_events=_HISTORY_LIMIT,
            )

            return decision, data

        except (
            ValidationError,
            SearchNormalizerError,
        ) as exc:
            if attempt >= _MAX_DECISION_RETRIES:
                raise SearchNormalizerError(
                    "Normalizer вернул некорректный план поиска."
                ) from exc

            correction = HumanMessage(
                content=(
                    "Предыдущий JSON не прошёл проверку:\n"
                    f"{exc}\n\n"
                    "Верни только корректный JSON по схеме "
                    "SearchNormalizationDecision. Используй только "
                    "candidate id, реально присутствующие в verified "
                    "task history."
                )
            )

            conversation.append(correction)

            llm = llm_client.bind_tools(
                [lookup_entity],
                worker_kind="search_normalizer",
            )

            response = await llm.ainvoke(conversation)
            conversation.append(response)

            if getattr(response, "tool_calls", None):
                raise SearchNormalizerError(
                    "Нельзя продолжать tool calls во время JSON correction."
                )

            content = getattr(response, "content", None)
            attempt_text = (
                content.strip()
                if isinstance(content, str)
                else ""
            )

    raise AssertionError("unreachable")


def _validate_referenced_candidates(
    *,
    decision: SearchNormalizationDecision,
    snapshot_data: dict[str, Any],
) -> None:
    known_ids = _known_candidate_ids(snapshot_data)

    referenced_ids = {
        *decision.used_candidate_ids,
        *decision.option_ids,
    }

    unknown_ids = referenced_ids - known_ids

    if unknown_ids:
        raise SearchNormalizerError(
            "Decision references unknown catalog candidates: "
            f"{sorted(unknown_ids)}"
        )


def _known_candidate_ids(
    snapshot_data: dict[str, Any],
) -> set[str]:
    known: set[str] = set()

    for event in get_agent_events(
        snapshot_data,
        agent=_AGENT_NAME,
    ):
        if event["role"] != "tool":
            continue

        if event["kind"] != "lookup_entity":
            continue

        candidates = event.get("payload", {}).get(
            "candidates",
            [],
        )

        if not isinstance(candidates, list):
            continue

        for raw in candidates:
            if not isinstance(raw, dict):
                continue

            candidate_id = raw.get("id")

            if isinstance(candidate_id, str) and candidate_id:
                known.add(candidate_id)

    return known


def _parse_tool_call(
    raw_call: object,
) -> tuple[str, str | None]:
    if isinstance(raw_call, dict):
        tool_call_id = raw_call.get("id") or "unknown"
        args = raw_call.get("args") or {}
    else:
        tool_call_id = getattr(raw_call, "id", None) or "unknown"
        args = getattr(raw_call, "args", None) or {}

    raw_value = (
        args.get("raw_value")
        if isinstance(args, dict)
        else None
    )

    if not isinstance(raw_value, str) or not raw_value.strip():
        return str(tool_call_id), None

    return str(tool_call_id), raw_value.strip()


def _parse_candidates(
    raw_result: object,
) -> list[CatalogCandidate]:
    if not isinstance(raw_result, list):
        return []

    candidates: list[CatalogCandidate] = []

    for item in raw_result:
        if not isinstance(item, dict):
            continue

        try:
            candidates.append(
                CatalogCandidate.model_validate(item)
            )
        except ValidationError:
            continue

    return candidates