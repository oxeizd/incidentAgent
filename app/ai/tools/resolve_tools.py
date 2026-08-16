"""
app/ai/tools/resolve_tools.py

ask_user_clarify теперь проверяет resume-значение на отклонение
(check_not_deviation) — раньше этот тул вызывал interrupt() и слепо
возвращал что бы ни пришло обратно как "выбор пользователя". Передаём
options — если пользователь ответил ровно одним из предложенных id,
LLM-проверка не нужна (гарантированный ответ).
"""
from __future__ import annotations
from typing import List, Dict, Any, Annotated

from langchain_core.tools import tool, InjectedToolArg
from langgraph.types import interrupt

from app.services.entity_resolver import lookup_entities
from app.ai.graph.interrupts import ask_user
from app.ai.runtime.node_kit import check_not_deviation


@tool
async def lookup_entity(raw_value: str) -> List[Dict[str, Any]]:
    """
    Ищет точное каноническое значение для КАТАЛОЖНОГО поля (system_name,
    work_group, executor_name, element_name, created_by) по неточному
    названию/ФИО/жаргону. НЕ вызывай на даты, числа, месяцы и служебные
    слова ("инциденты", "поручения", "за", "число") — это не сущности.
    Возвращает кандидатов с score (каждый включает entity_type).
    """
    return await lookup_entities(raw_value, top_k=5)


@tool
async def ask_user_clarify(
    question: str,
    options: list[str],
    worker_id: Annotated[str, InjectedToolArg],
    round: Annotated[int, InjectedToolArg],
) -> str:
    """
    Задать пользователю уточняющий вопрос — например, когда lookup_entity
    вернул несколько кандидатов и по контексту нельзя выбрать однозначно.
    worker_id/round НЕ заполняются моделью — InjectedToolArg, подставляются
    вызывающим кодом (см. graph/nodes_search.py::resolve_node).
    """
    raw = interrupt(ask_user(question, worker_id, kind="clarify_entity", round=round, options=options))
    await check_not_deviation(question, raw, options=options)
    return raw