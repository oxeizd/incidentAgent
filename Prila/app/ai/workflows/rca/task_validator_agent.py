from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.runtime.services import get_memory
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.rca.contracts import (
    ProposedTask,
    RCAValidationResult,
    ValidatedTask,
)
from app.services.llm import llm_client


_AGENT_NAME = "rca_task_validator"
_HISTORY_LIMIT = 30
_SIMILAR_LIMIT = 5


class TaskValidationVerdict(BaseModel):
    """
    Verdict одной меры в исходном порядке tasks.

    most_similar_index относится только к списку similar_assignments именно
    этой меры, начиная с 1. Если мера NEW или INVALID — индекс null.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        pattern="^(NEW|PARTIAL|DUPLICATE|INVALID)$"
    )
    reason: str = Field(min_length=1, max_length=2_000)
    most_similar_index: int | None = Field(
        default=None,
        ge=1,
    )


class TaskValidationBatch(BaseModel):
    """
    LLM обязан вернуть verdict ровно для каждой переданной меры и в том же
    порядке. Runtime дополнительно проверит количество результатов.
    """

    model_config = ConfigDict(extra="forbid")

    results: list[TaskValidationVerdict] = Field(
        default_factory=list
    )


_VALIDATOR_PROMPT = """
Ты — валидатор системных мер RCA-справки.

Тебе передан список новых proposed actions и для каждой — до пяти похожих
существующих поручений из базы.

Верни verdict для КАЖДОЙ новой меры в том же порядке.

Статусы:

- NEW: в похожих поручениях нет меры с тем же механизмом и целью.
- PARTIAL: есть пересечение, но новая мера покрывает иной scope, компонент,
  условие, механизм или измеримый expected result.
- DUPLICATE: существующая мера фактически уже требует тот же механизм и
  ожидаемый результат; новых существенных деталей нет.
- INVALID: мера неконкретна, неустранимая связь с RCA не указана, нет
  проверяемого результата, либо это общее пожелание вместо механизма.

Правила:
- Не помечай как duplicate только из-за общих слов вроде «мониторинг»,
  «контроль», «проверка» или «настройка».
- most_similar_index указывай только для PARTIAL/DUPLICATE и только как
  номер существующего поручения в собственном списке этой меры.
- Для NEW/INVALID most_similar_index должен быть null.
- reason должен коротко объяснять сходство, отличие или проблему меры.
- Не создавай, не переписывай и не удаляй actions.
- Верни JSON строго по схеме TaskValidationBatch.
"""


@dataclass(frozen=True, slots=True)
class TaskValidationOutcome:
    validation: RCAValidationResult
    snapshot_data: dict[str, Any]


class TaskValidatorError(RuntimeError):
    """Контролируемая ошибка task validation."""


async def validate_rca_tasks(
    *,
    snapshot: TaskSnapshot,
    corrective_actions: list[ProposedTask],
    preventive_actions: list[ProposedTask],
) -> TaskValidationOutcome:
    """
    Валидирует all actions одним LLM batch call.

    Порядок сохраняется: сначала corrective, затем preventive. Тип action
    сохраняем отдельно, чтобы report persistence мог положить validated
    tasks обратно в соответствующие sections.
    """
    indexed_tasks = [
        ("corrective", task)
        for task in corrective_actions
    ] + [
        ("preventive", task)
        for task in preventive_actions
    ]

    if not indexed_tasks:
        empty = RCAValidationResult()

        data = append_agent_event(
            snapshot.data,
            agent=_AGENT_NAME,
            role="assistant",
            kind="empty_validation",
            payload=empty.model_dump(mode="json"),
            max_events=_HISTORY_LIMIT,
        )

        return TaskValidationOutcome(
            validation=empty,
            snapshot_data=data,
        )

    similar_by_task = await _retrieve_similar_assignments(
        [
            task
            for _, task in indexed_tasks
        ]
    )

    blocks = [
        _format_validation_block(
            index=index,
            category=category,
            task=task,
            similar_assignments=similar,
        )
        for index, (
            (category, task),
            similar,
        ) in enumerate(
            zip(indexed_tasks, similar_by_task),
            start=1,
        )
    ]

    data = append_agent_event(
        snapshot.data,
        agent=_AGENT_NAME,
        role="system",
        kind="validation_input",
        payload={
            "tasks": [
                {
                    "category": category,
                    "task": task.model_dump(mode="json"),
                    "similar_assignments": similar,
                }
                for (category, task), similar in zip(
                    indexed_tasks,
                    similar_by_task,
                )
            ]
        },
        max_events=_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_VALIDATOR_PROMPT,
        extra_context={
            "task_count": len(indexed_tasks),
            "agent_history": get_agent_events(
                data,
                agent=_AGENT_NAME,
            ),
        },
        output_contract="JSON строго по схеме TaskValidationBatch.",
    )

    batch = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content="\n\n".join(blocks)),
        ],
        TaskValidationBatch,
        worker_kind="rca_task_validator",
    )

    if len(batch.results) != len(indexed_tasks):
        raise TaskValidatorError(
            "Task validator returned wrong number of verdicts."
        )

    validation = _build_validation_result(
        indexed_tasks=indexed_tasks,
        similar_by_task=similar_by_task,
        verdicts=batch.results,
    )

    data = append_agent_event(
        data,
        agent=_AGENT_NAME,
        role="assistant",
        kind="validation_result",
        payload=validation.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    return TaskValidationOutcome(
        validation=validation,
        snapshot_data=data,
    )


async def _retrieve_similar_assignments(
    tasks: list[ProposedTask],
) -> list[list[dict[str, Any]]]:
    """
    Read-only retrieval похожих поручений.

    Если retrieval временно недоступен, не отменяем RCA: передаём LLM
    пустой список похожих поручений. Это не означает, что мера NEW —
    validator всё равно должен дать verdict только из доступного контекста.
    """
    memory = get_memory()
    results: list[list[dict[str, Any]]] = []

    for task in tasks:
        query_text = (
            f"{task.title}\n{task.description}"
        ).strip()

        try:
            response = (
                await memory.find_similar_assignments_for_agent(
                    query_text=query_text,
                    limit=_SIMILAR_LIMIT,
                )
            )
            raw_results = response.get("results") or []
        except Exception:
            raw_results = []

        results.append(
            [
                item
                for item in raw_results
                if isinstance(item, dict)
            ]
        )

    return results


def _format_validation_block(
    *,
    index: int,
    category: str,
    task: ProposedTask,
    similar_assignments: list[dict[str, Any]],
) -> str:
    """
    Строит LLM input только из task и фактически полученных similar results.
    """
    lines = [
        f"Новая мера {index} ({category}):",
        f"Заголовок: {task.title}",
        f"Описание: {task.description}",
        f"Устраняет: {task.addresses}",
        f"Тип: {task.type}",
        f"Приоритет: {task.priority}",
        f"Ожидаемый результат: {task.expected_result}",
        "",
        "Похожие существующие поручения:",
    ]

    if not similar_assignments:
        lines.append("Не найдены.")
        return "\n".join(lines)

    for similar_index, item in enumerate(
        similar_assignments,
        start=1,
    ):
        lines.extend(
            [
                f"{similar_index}. {item.get('assignment', '—')}",
                f"   Инцидент: {item.get('incident_id', '—')}",
                f"   Ответственный: {item.get('responsible', '—')}",
                f"   Статус: {item.get('status', '—')}",
            ]
        )

    return "\n".join(lines)


def _build_validation_result(
    *,
    indexed_tasks: list[tuple[str, ProposedTask]],
    similar_by_task: list[list[dict[str, Any]]],
    verdicts: list[TaskValidationVerdict],
) -> RCAValidationResult:
    """
    Формирует accepted/rejected tasks.

    DUPLICATE остаётся accepted: это валидная мера, но с маркировкой, чтобы
    report/UI мог показать пользователю, что её стоит связать с существующим
    поручением вместо создания нового. INVALID — единственный статус,
    который не попадает в финальный report tasks.
    """
    accepted: list[ValidatedTask] = []
    rejected: list[ValidatedTask] = []

    for (
        (_, task),
        similar,
        verdict,
    ) in zip(
        indexed_tasks,
        similar_by_task,
        verdicts,
    ):
        most_similar = None

        if verdict.most_similar_index is not None:
            index = verdict.most_similar_index - 1

            if 0 <= index < len(similar):
                most_similar = similar[index]

        validated = ValidatedTask(
            **task.model_dump(),
            validation_status=verdict.status,
            validation_reason=verdict.reason,
            most_similar_assignment=most_similar,
        )

        if verdict.status == "INVALID":
            rejected.append(validated)
        else:
            accepted.append(validated)

    return RCAValidationResult(
        accepted_tasks=accepted,
        rejected_tasks=rejected,
    )