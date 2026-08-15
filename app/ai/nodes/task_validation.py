from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from app.ai.prompts.registry import get_prompt
from app.memory.repository.assignments import search_assignments
from app.services.llm import llm_client

_VALIDATOR_FALLBACK_PROMPT = (
    "Ты — эксперт по проверке поручений. Сравни каждое новое поручение с "
    "похожими из базы: DUPLICATE, PARTIAL или NEW."
)


class TaskValidation(BaseModel):
    status: Literal["DUPLICATE", "PARTIAL", "NEW"] = "NEW"
    most_similar_index: Optional[int] = None
    reason: str = ""


class ValidationBatch(BaseModel):
    results: list[TaskValidation] = Field(default_factory=list)


def _format_similar_assignments(assignments: list[dict]) -> str:
    if not assignments:
        return "(похожих поручений не найдено)"
    return "\n".join(
        f"{i}. {a.get('assignment', '')} (инцидент {a.get('incident_id', '?')}, ответственный: {a.get('responsible', '?')})"
        for i, a in enumerate(assignments, 1)
    )


async def validate_tasks(tasks: list[dict]) -> list[dict]:
    """
    Батч-валидация (один LLM-вызов на все задачи разом, не по одному):
    для каждой задачи ищет похожие поручения в БД (search_assignments) и
    классифицирует DUPLICATE/PARTIAL/NEW. Возвращает список с добавленными
    validation_status/validation_reason/most_similar_assignment.
    Работает и с одной задачей (editor::_replace_task), и со всем списком
    (rca::task_validator).
    """
    if not tasks:
        return []

    similar_by_task: list[list[dict]] = []
    for task in tasks:
        try:
            found = await search_assignments({"text_query": task["title"], "limit": 3})
        except Exception:
            found = []
        similar_by_task.append(found)

    blocks = []
    for i, (task, similar) in enumerate(zip(tasks, similar_by_task), 1):
        blocks.append(
            f"Новое поручение {i}:\nЗаголовок: {task['title']}\nОписание: {task['description']}\n"
            f"Ожидаемый результат: {task['expected_result']}\n\n"
            f"Найденные похожие поручения в базе:\n{_format_similar_assignments(similar)}\n---"
        )
    human_text = "\n".join(blocks)

    system = llm_client.build_system_message(role_instruction=get_prompt("rca_task_validator", fallback=_VALIDATOR_FALLBACK_PROMPT))
    batch = await llm_client.ainvoke_structured(
        [system, HumanMessage(content=human_text)], ValidationBatch, worker_kind="rca",
    )

    validated: list[dict] = []
    for i, (task, similar) in enumerate(zip(tasks, similar_by_task)):
        verdict = batch.results[i] if i < len(batch.results) else TaskValidation(status="NEW", reason="Нет результата валидации")
        similar_item = None
        if verdict.most_similar_index and 1 <= verdict.most_similar_index <= len(similar):
            similar_item = similar[verdict.most_similar_index - 1]
        validated.append({
            **task,
            "validation_status": verdict.status,
            "validation_reason": verdict.reason,
            "most_similar_assignment": similar_item,
        })
    return validated