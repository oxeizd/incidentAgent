from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.presentation.contracts import (
    PresentationAnswerUpdate,
    PresentationCollectionDecision,
    PresentationSource,
)
from app.memory.artifacts.presentations.document import (
    PresentationDocument,
)
from app.services.llm import llm_client


_COLLECTOR_AGENT = "presentation_collector"
_HISTORY_LIMIT = 32
_MAX_DOCUMENT_RETRIES = 2


_COLLECTOR_PROMPT = """
Ты — агент сбора данных для презентации IT-инцидента.

Тебе переданы:
- источник презентации: incident, RCA-справка или описание пользователя;
- проверенные данные из этого источника;
- уже собранные поля будущего PresentationDocument;
- локальная история текущей задачи.

Твоя задача — собрать полезный PresentationDocument, не выдумывая факты.

Правила источников:

- Данные из готовой RCA-справки имеют приоритет для cause, chain, impact,
  operational_measures, systemic_measures, analysis_markdown и assignments.
- Данные incident имеют приоритет для number, description, system/team,
  временных полей и timeline.
- Из свободного описания извлекай только явно подтверждённые сведения.
- Не придумывай номера, даты, системы, команды, потери, метрики, причины,
  меры, alerts или поручения.
- Не затирай подтверждённое поле значением "—".
- Не используй "—" как доказательство отсутствия данных.

Полезная презентация может быть создана с неполными данными. Не требуй
поля только потому, что они существуют в PresentationDocument.

Верни:

- action="ready", если имеющихся данных достаточно, чтобы сформировать
  осмысленную презентацию. В fields верни полный JSON payload, совместимый
  с PresentationDocument.

- action="clarify", если без дополнительной информации презентация будет
  бессодержательной либо пользователь явно просил включить неизвестные
  критичные сведения. В fields верни уже известные поля, missing_fields —
  короткие имена реально нужных сведений, question — один понятный вопрос.

Примеры критичных пробелов:
- пользователь просит презентацию «по инциденту», но не указал ни номера,
  ни описания, ни RCA-справки;
- пользователь явно просит указать потери, но в source их нет;
- нужен уровень детализации или цель аудитории, а без него невозможно
  исполнить явно заданный формат.

Не спрашивай номер, root cause, impact или timeline автоматически, если
в презентации уже есть достаточно содержания из доступного источника.

detail_level:
- brief — если пользователь просит кратко/для руководства;
- detailed — если просит подробную версию/для комиссии;
- standard — иначе.

Возвращай только JSON строго по схеме PresentationCollectionDecision.
"""


_ANSWER_UPDATE_PROMPT = """
Ты дополняешь данные презентации IT-инцидента по обычному текстовому ответу
пользователя.

Тебе переданы:
- вопрос, который был задан;
- поля, которых не хватало;
- текущие собранные поля PresentationDocument;
- текстовый ответ пользователя.

Верни fields_update только с полями, которые явно или надёжно следуют из
ответа. Не выдумывай факты и не возвращай поля, которые ответ не уточняет.

Если пользователь дал информацию, покрывающую несколько полей, заполни все
такие поля. Например, описание причины может также уточнить brief,
description, cause и chain.

Не возвращай "—" для неизвестных полей: просто не включай их в
fields_update.

Верни только JSON строго по схеме PresentationAnswerUpdate.
"""


@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    decision: PresentationCollectionDecision
    document: PresentationDocument | None
    snapshot_data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AnswerUpdateOutcome:
    fields_update: dict[str, Any]
    snapshot_data: dict[str, Any]


class PresentationCollectionError(RuntimeError):
    """Контролируемая ошибка сбора данных презентации."""


async def collect_presentation(
    *,
    snapshot: TaskSnapshot,
    source: PresentationSource,
    collected_fields: dict[str, Any],
    user_request: str,
) -> CollectionOutcome:
    """
    Запускает collector и валидирует ready payload существующей schema
    PresentationDocument.

    При `clarify` document остаётся None: workflow хранит частично собранные
    fields и ждёт следующий user text.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_COLLECTOR_AGENT,
        role="system",
        kind="collection_input",
        payload={
            "source": source.model_dump(mode="json"),
            "collected_fields": collected_fields,
            "user_request": user_request,
        },
        max_events=_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_COLLECTOR_PROMPT,
        extra_context={
            "source": source.model_dump(mode="json"),
            "collected_fields": collected_fields,
            "agent_history": get_agent_events(
                data,
                agent=_COLLECTOR_AGENT,
            ),
        },
        output_contract=(
            "JSON строго по схеме "
            "PresentationCollectionDecision."
        ),
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_request),
        ],
        PresentationCollectionDecision,
        worker_kind="presentation_collector",
    )

    document: PresentationDocument | None = None

    if decision.action == "ready":
        document = await _validate_document_with_retry(
            decision=decision,
            system=system,
            user_request=user_request,
        )

        decision = decision.model_copy(
            update={
                "fields": document.model_dump(mode="json"),
            }
        )

    data = append_agent_event(
        data,
        agent=_COLLECTOR_AGENT,
        role="assistant",
        kind="collection_decision",
        payload=decision.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    return CollectionOutcome(
        decision=decision,
        document=document,
        snapshot_data=data,
    )


async def extract_answer_update(
    *,
    snapshot: TaskSnapshot,
    question: str,
    missing_fields: list[str],
    collected_fields: dict[str, Any],
    user_text: str,
) -> AnswerUpdateOutcome:
    """
    Извлекает только новые поля из текстового ответа пользователя.

    Merge выполняется workflow детерминированно: новые непустые поля
    дополняют collected_fields, после чего collector повторно оценивает
    достаточность итогового документа.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_COLLECTOR_AGENT,
        role="user",
        kind="clarification_answer",
        payload={
            "question": question,
            "missing_fields": missing_fields,
            "text": user_text,
        },
        max_events=_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_ANSWER_UPDATE_PROMPT,
        extra_context={
            "question": question,
            "missing_fields": missing_fields,
            "collected_fields": collected_fields,
        },
        output_contract=(
            "JSON строго по схеме PresentationAnswerUpdate."
        ),
    )

    update = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=user_text),
        ],
        PresentationAnswerUpdate,
        worker_kind="presentation_collector",
    )

    data = append_agent_event(
        data,
        agent=_COLLECTOR_AGENT,
        role="assistant",
        kind="extracted_fields_update",
        payload=update.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    return AnswerUpdateOutcome(
        fields_update=update.fields_update,
        snapshot_data=data,
    )


async def _validate_document_with_retry(
    *,
    decision: PresentationCollectionDecision,
    system: Any,
    user_request: str,
) -> PresentationDocument:
    """
    PresentationDocument validation — внешний contract persistence layer.

    Если collector вернул несовместимый payload, не подчищаем его Python-ом:
    даём LLM ограниченное число шансов вернуть валидный полный document.
    """
    current_decision = decision

    for attempt in range(_MAX_DOCUMENT_RETRIES + 1):
        try:
            return PresentationDocument.model_validate(
                current_decision.fields
            )
        except ValidationError as exc:
            if attempt >= _MAX_DOCUMENT_RETRIES:
                raise PresentationCollectionError(
                    "Collector returned invalid PresentationDocument."
                ) from exc

            correction = (
                "Предыдущий JSON fields не прошёл валидацию "
                "PresentationDocument:\n"
                f"{exc}\n\n"
                "Верни JSON строго по схеме "
                "PresentationCollectionDecision с action='ready' "
                "и полным валидным fields."
            )

            corrected = await llm_client.ainvoke_structured(
                [
                    system,
                    HumanMessage(content=user_request),
                    HumanMessage(content=correction),
                ],
                PresentationCollectionDecision,
                worker_kind="presentation_collector",
            )

            if corrected.action != "ready":
                raise PresentationCollectionError(
                    "Collector changed ready decision during "
                    "document correction."
                )

            current_decision = corrected

    raise AssertionError("unreachable")