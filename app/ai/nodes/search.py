"""Ноды search-подграфа (resolve_entity -> search).

Tool artifacts теперь имеют стабильный контракт SearchPage:
- total_count — точное число совпадений до pagination;
- items — текущая страница сырых записей;
- display — поля для чата из data/config/search_output.yaml.

Поэтому итоговый summary не использует len(tool_results) как количество
найденных инцидентов/поручений: это число только вызовов tools, а не число
записей в БД.
"""
from __future__ import annotations

from typing import Any

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeCtx, worker_node
from app.ai.runtime.tool_agent import ToolLoopAgent
from app.ai.tools.registry import get_tools

_RESOLVE_MAX_ITERATIONS = 8
_SEARCH_MAX_ITERATIONS = 2
_INTERRUPTING_TOOLS = frozenset({"ask_user_clarify"})
_SEARCH_RESULT_TAGS = {
    "search_incidents_tool": "incident",
    "search_assignments_tool": "assignment",
}
_SEARCH_OUTPUT_CONTRACT = """Ты обязан вызвать доступные search tools, если пользователь просит найти данные.
Для утверждений «сколько найдено» используй ТОЛЬКО artifact.total_count.
Для перечисления используй artifact.display.rows или artifact.items текущей страницы.
Если artifact.has_more=true, явно сообщи, что показана одна страница, и для следующей страницы используй offset + returned_count.
Не считай количество по len(items), если отвечаешь о полном результате."""


@worker_node("resolve_entity")
async def resolve_node(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    ctx.log(f"Резолвлю запрос: {payload.raw_query!r}")

    agent = ToolLoopAgent(
        role="resolve_entity",
        system_prompt=get_prompt("resolver"),
        tools=get_tools("resolve_entity"),
        max_iterations=_RESOLVE_MAX_ITERATIONS,
        interrupting_tools=_INTERRUPTING_TOOLS,
    )
    result = await agent.run(ctx, user_text=payload.raw_query)
    search_query = ((result.final_text or "").strip()) or payload.raw_query

    return ctx.running(
        message="Разобрал запрос, перехожу к поиску",
        payload_update={"search_query": search_query},
    )


@worker_node("search")
async def search_node(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    ctx.log(f"Ищу: {payload.search_query!r}")

    agent = ToolLoopAgent(
        role="search",
        system_prompt=get_prompt("search"),
        tools=get_tools("search"),
        max_iterations=_SEARCH_MAX_ITERATIONS,
        result_tags=_SEARCH_RESULT_TAGS,
        output_contract=_SEARCH_OUTPUT_CONTRACT,
    )
    result = await agent.run(ctx, user_text=payload.search_query)
    artifacts = [artifact for artifact in result.tool_results if _is_search_page(artifact)]

    if not artifacts:
        return ctx.failed(code="no_results", message="Не нашёл релевантных инцидентов или поручений по запросу.")

    total_count = sum(int(artifact["total_count"]) for artifact in artifacts)
    returned_count = sum(int(artifact["returned_count"]) for artifact in artifacts)
    has_more = any(bool(artifact["has_more"]) for artifact in artifacts)
    analysis_items = [
        item
        for artifact in artifacts
        for item in artifact["items"]
    ]

    return ctx.done(
        message=f"Найдено записей: {total_count}; показано: {returned_count}",
        summary={
            "total_count": total_count,
            "returned_count": returned_count,
            "has_more": has_more,
            "pages": artifacts,
            "results": analysis_items,
        },
        payload_update={
            "search_pages": artifacts,
            "search_results": analysis_items,
            "search_total_count": total_count,
        },
    )


def _is_search_page(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("entity"), str)
        and isinstance(value.get("total_count"), int)
        and isinstance(value.get("returned_count"), int)
        and isinstance(value.get("items"), list)
        and isinstance(value.get("display"), dict)
    )