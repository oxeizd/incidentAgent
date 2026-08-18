from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.services import get_memory
from app.services.llm import llm_client


_VALIDATION_FALLBACK_PROMPT = """
Ты валидируешь новые системные меры по инциденту.

Для каждой новой меры сравни её с похожими существующими поручениями.

Верни:
- NEW — меры с похожим смыслом нет;
- PARTIAL — похожая мера есть, но новая покрывает иной scope/деталь;
- DUPLICATE — новая мера фактически повторяет существующую.

Не называй меру duplicate только по совпадению общих слов.
"""


class TaskValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["NEW", "PARTIAL", "DUPLICATE"] = "NEW"
    most_similar_index: int | None = Field(default=None, ge=1)
    reason: str = ""


class TaskValidationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[TaskValidation] = Field(default_factory=list)


async def validate_tasks(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Валидирует все RCA задачи одним LLM batch call.

    Для каждой меры semantic retrieval возвращает максимум 5 похожих
    поручений. Эти данные используются только как context для verdict.
    """
    if not tasks:
        return []

    similar_by_task: list[list[dict[str, Any]]] = []

    for task in tasks:
        title = str(task.get("title") or "").strip()
        description = str(task.get("description") or "").strip()

        query_text = (
            f"{title}\n{description}".strip()
            or "системная мера по инциденту"
        )

        try:
            response = await get_memory().find_similar_assignments_for_agent(
                query_text=query_text,
                limit=5,
            )
            similar = response.get("results") or []
        except Exception:
            similar = []

        similar_by_task.append(
            [
                item
                for item in similar
                if isinstance(item, dict)
            ]
        )

    blocks = [
        _format_task_block(
            index=index,
            task=task,
            similar=similar,
        )
        for index, (task, similar) in enumerate(
            zip(tasks, similar_by_task),
            start=1,
        )
    ]

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "rca_task_validator",
            fallback=_VALIDATION_FALLBACK_PROMPT,
        ),
        output_contract="JSON строго по схеме TaskValidationBatch.",
    )

    batch = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content="\n\n".join(blocks)),
        ],
        TaskValidationBatch,
        worker_kind="rca",
    )

    validated: list[dict[str, Any]] = []

    for index, task in enumerate(tasks):
        verdict = (
            batch.results[index]
            if index < len(batch.results)
            else TaskValidation(
                status="NEW",
                reason="Валидатор не вернул результат для этой меры.",
            )
        )

        similar = similar_by_task[index]
        most_similar = None

        if (
            verdict.most_similar_index is not None
            and 1 <= verdict.most_similar_index <= len(similar)
        ):
            most_similar = similar[
                verdict.most_similar_index - 1
            ]

        validated.append(
            {
                **task,
                "validation_status": verdict.status,
                "validation_reason": verdict.reason,
                "most_similar_assignment": most_similar,
            }
        )

    return validated


def _format_task_block(
    *,
    index: int,
    task: dict[str, Any],
    similar: list[dict[str, Any]],
) -> str:
    task_text = "\n".join(
        [
            f"Новая мера {index}:",
            f"Заголовок: {task.get('title', '')}",
            f"Описание: {task.get('description', '')}",
            f"Устраняет: {task.get('addresses', '')}",
            f"Ожидаемый результат: {task.get('expected_result', '')}",
        ]
    )

    if not similar:
        return (
            f"{task_text}\n\n"
            "Похожие поручения: не найдены."
        )

    similar_lines = []

    for similar_index, item in enumerate(similar, start=1):
        similar_lines.append(
            "\n".join(
                [
                    f"{similar_index}. {item.get('assignment', '—')}",
                    f"   Инцидент: {item.get('incident_id', '—')}",
                    f"   Ответственный: {item.get('responsible', '—')}",
                    f"   Статус: {item.get('status', '—')}",
                ]
            )
        )

    return (
        f"{task_text}\n\n"
        "Похожие поручения:\n"
        + "\n".join(similar_lines)
    )