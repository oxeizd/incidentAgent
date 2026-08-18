from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeContext, worker_node
from app.ai.runtime.tool_agent import ToolLoopAgent
from app.ai.tools.registry import get_tools
from app.services.llm import llm_client


_RESOLVER_FALLBACK_PROMPT = """
Ты — планировщик resolver этапа поиска по инцидентам и поручениям.

Твоя задача — вернуть строго JSON по схеме ResolverDecision.

Каталожные сущности:
- system_name;
- work_group;
- executor_name;
- element_name;
- created_by.

Выбирай action="ask_user" ТОЛЬКО когда нельзя продолжать поиск без выбора
между конкретными значениями. В этом случае:
- question — короткий понятный вопрос;
- options — минимум два взаимоисключающих варианта;
- resolved_query — полный запрос с уже известным контекстом.

Выбирай action="search" во всех остальных случаях:
- есть номер инцидента;
- указан период, метрика, статус или приоритет;
- дано свободное описание проблемы;
- каталожная сущность отсутствует;
- уточнение не является критичным для поиска.

entity_text:
- заполняй конкретным неточным названием системы, команды, элемента или ФИО,
  если его стоит проверить через lookup_entity;
- не используй для номера инцидента, даты, статуса, приоритета, чисел,
  метрик, свободного описания или служебных слов.

Не задавай вопрос ради вопроса.
"""

_SEARCH_FALLBACK_PROMPT = """
Ты — search agent по ИТ-инцидентам и поручениям.

Выполняй поиск через доступные tools.

Правила выбора инструмента:
- точный номер инцидента, статус, система, группа, дата, приоритет,
  длительность или иные точные фильтры — structured search;
- похожие инциденты по смыслу причины/описания — semantic search;
- похожие поручения по смыслу — semantic assignment search;
- можешь вызвать несколько tools, если это действительно требуется;
- не выдумывай результаты;
- не объясняй длинно: итог для пользователя строится вне tool loop.
"""


class ResolverDecision(BaseModel):
    """
    Строгий output contract resolver LLM.

    LLM никогда не делает interrupt сама. Она может только вернуть решение,
    после чего node вызывает ctx.ask() при action='ask_user'.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["search", "ask_user"]

    resolved_query: str = Field(
        min_length=1,
        description=(
            "Самодостаточный нормализованный поисковый запрос."
        ),
    )

    entity_text: str | None = Field(
        default=None,
        description=(
            "Неточное значение system_name/work_group/executor_name/"
            "element_name/created_by для optional lookup_entity."
        ),
    )

    question: str | None = Field(
        default=None,
        description=(
            "Вопрос пользователю только если action='ask_user'."
        ),
    )

    options: list[str] = Field(
        default_factory=list,
        description=(
            "Варианты выбора только если action='ask_user'."
        ),
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ResolverDecision":
        if self.action != "ask_user":
            return self

        if not self.question or not self.question.strip():
            raise ValueError(
                "question is required for action='ask_user'"
            )

        normalized_options = [
            option.strip()
            for option in self.options
            if option and option.strip()
        ]

        if len(normalized_options) < 2:
            raise ValueError(
                "at least two options are required for action='ask_user'"
            )

        if len(normalized_options) != len(set(normalized_options)):
            raise ValueError(
                "options for action='ask_user' must be unique"
            )

        self.options = normalized_options
        return self


def _append_selected_entity(
    *,
    query: str,
    selected_value: str,
) -> str:
    return (
        f"{query}\n\n"
        f"Выбранное точное каталожное значение: {selected_value}"
    )


async def _lookup_candidates(
    entity_text: str,
) -> list[dict[str, Any]]:
    tools = get_tools("resolve_entity")

    if not tools:
        return []

    lookup_tool = tools[0]

    response = await lookup_tool.ainvoke(
        {
            "raw_value": entity_text,
        }
    )

    if not isinstance(response, list):
        return []

    return [
        candidate
        for candidate in response
        if isinstance(candidate, dict)
    ]


def _candidate_names(
    candidates: list[dict[str, Any]],
) -> list[str]:
    names: list[str] = []

    for candidate in candidates:
        name = candidate.get("name")

        if isinstance(name, str) and name.strip():
            names.append(name.strip())

    return list(dict.fromkeys(names))


async def _build_resolver_decision(
    raw_query: str,
) -> ResolverDecision:
    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "resolver",
            fallback=_RESOLVER_FALLBACK_PROMPT,
        ),
        output_contract="JSON строго по схеме ResolverDecision.",
    )

    return await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=raw_query),
        ],
        ResolverDecision,
        worker_kind="search",
    )


@worker_node("resolve_entity")
async def resolve_entity(
    ctx: NodeContext,
) -> dict:
    """
    Нормализует query и при необходимости спрашивает выбор entity.

    Safe replay:
    - до ctx.ask() нет DB writes;
    - LLM structured plan повторяется при resume;
    - ctx.ask() с тем же question/options получает resume value;
    - после resume node продолжает и возвращает state update.
    """
    payload = ctx.typed.payload

    ctx.log(
        "Проверяю параметры и каталожные сущности поискового запроса.",
        stage="entity_resolution",
    )

    decision = await _build_resolver_decision(
        payload.raw_query,
    )

    # Explicit Pydantic decision: вопрос появился потому, что resolver
    # вернул action='ask_user', а не потому что модель вызвала tool.
    if decision.action == "ask_user":
        selected = await ctx.ask(
            decision.question or "Уточните нужный вариант.",
            options=decision.options,
            metadata={
                "purpose": "entity_resolution",
                "resolved_query": decision.resolved_query,
            },
        )

        selected_text = str(selected).strip()

        if selected_text not in decision.options:
            return ctx.failed(
                code="invalid_entity_selection",
                message=(
                    "Выбранный вариант отсутствует среди предложенных "
                    "значений."
                ),
            )

        return ctx.running(
            message="Уточнение получено, параметры поиска подготовлены.",
            payload_update={
                "resolved_query": _append_selected_entity(
                    query=decision.resolved_query,
                    selected_value=selected_text,
                ),
            },
        )

    resolved_query = decision.resolved_query

    # Optional canonicalization. lookup tool read-only, поэтому replay node
    # до interrupt не создаёт side effects. Если каталог вернул ambiguity,
    # node сама задаёт вопрос, не полагаясь на tool/LLM function call.
    if decision.entity_text:
        ctx.log(
            "Сверяю значение с каталогом сущностей.",
            stage="entity_lookup",
        )

        candidates = await _lookup_candidates(
            decision.entity_text,
        )

        if len(candidates) == 1:
            canonical_name = candidates[0].get("name")

            if isinstance(canonical_name, str) and canonical_name.strip():
                resolved_query = _append_selected_entity(
                    query=resolved_query,
                    selected_value=canonical_name.strip(),
                )

        elif len(candidates) > 1:
            options = _candidate_names(candidates)

            if len(options) >= 2:
                selected = await ctx.ask(
                    (
                        "Нашёл несколько подходящих значений. "
                        "Выберите нужное:"
                    ),
                    options=options,
                    metadata={
                        "purpose": "entity_resolution",
                        "resolved_query": resolved_query,
                    },
                )

                selected_text = str(selected).strip()

                if selected_text not in options:
                    return ctx.failed(
                        code="invalid_entity_selection",
                        message=(
                            "Выбранный вариант отсутствует среди "
                            "предложенных значений."
                        ),
                    )

                resolved_query = _append_selected_entity(
                    query=resolved_query,
                    selected_value=selected_text,
                )

    return ctx.running(
        message="Параметры поиска подготовлены.",
        payload_update={
            "resolved_query": resolved_query,
        },
    )


@worker_node("search")
async def search(
    ctx: NodeContext,
) -> dict:
    """
    Выполняет retrieval через read-only agent search tools.

    Search node не пишет UI message, не создаёт persisted SearchResult и
    не модифицирует memory. Итог может быть показан пользователю либо
    передан в RCA как summary_for_parent.
    """
    payload = ctx.typed.payload
    query = payload.resolved_query or payload.raw_query

    ctx.log(
        "Ищу релевантные инциденты и поручения.",
        stage="search",
    )

    agent = ToolLoopAgent(
        role="search",
        system_prompt=get_prompt(
            "search",
            fallback=_SEARCH_FALLBACK_PROMPT,
        ),
        tools=get_tools("search"),
        max_iterations=3,
        output_contract=(
            "Обязательно используй доступные tools для поиска. "
            "Не придумывай результаты."
        ),
    )

    outcome = await agent.run(
        ctx,
        user_text=query,
    )

    if not outcome.tool_results:
        return ctx.failed(
            code="no_results",
            message=(
                "Не нашёл релевантных инцидентов или поручений "
                "по этому запросу."
            ),
        )

    entities = {
        value
        for item in outcome.tool_results
        if isinstance(item, dict)
        and isinstance(
            value := item.get("_search_entity"),
            str,
        )
    }

    modes = {
        value
        for item in outcome.tool_results
        if isinstance(item, dict)
        and isinstance(
            value := item.get("_search_mode"),
            str,
        )
    }

    result_entity: Literal[
        "incidents",
        "assignments",
        "mixed",
    ] = (
        next(iter(entities))
        if len(entities) == 1
        and next(iter(entities)) in {"incidents", "assignments"}
        else "mixed"
    )

    search_mode: Literal[
        "structured",
        "semantic_similarity",
        "mixed",
    ] = (
        next(iter(modes))
        if len(modes) == 1
        and next(iter(modes))
        in {"structured", "semantic_similarity"}
        else "mixed"
    )

    compact_results = outcome.tool_results[:10]

    summary = {
        "query": query,
        "result_count": len(outcome.tool_results),
        "results": compact_results,
        "entity": result_entity,
        "mode": search_mode,
    }

    return ctx.done(
        message=f"Найдено результатов: {len(outcome.tool_results)}.",
        summary=summary,
        payload_update={
            "result_count": len(outcome.tool_results),
            "results": compact_results,
            "result_entity": result_entity,
            "search_mode": search_mode,
        },
    )