from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool

from app.ai.runtime.node_kit import NodeContext


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolLoopResult:
    """
    Результат agentic tool loop.

    tool_results — нормализованные dict-результаты tool вызовов.
    final_text — текст последнего LLM ответа, если модель решила завершить
    работу без дальнейших tool calls.
    """

    tool_results: list[dict[str, Any]]
    final_text: str | None = None


@dataclass(slots=True)
class ToolLoopAgent:
    """
    Универсальный LLM + tools loop.

    Используется только там, где LLM должен выбрать конкретный search tool
    и построить его аргументы. Interrupt-tools намеренно не поддерживаются:
    вопросы пользователю всегда контролируются node через Pydantic decision
    schema и NodeContext.ask().
    """

    role: str
    system_prompt: str
    tools: list[BaseTool]

    max_iterations: int = 3
    output_contract: str | None = None

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]

        if len(names) != len(set(names)):
            raise ValueError(
                f"ToolLoopAgent {self.role!r} has duplicate tool names"
            )

    async def run(
        self,
        context: NodeContext,
        *,
        user_text: str,
        extra_context: dict[str, Any] | None = None,
    ) -> ToolLoopResult:
        from app.services.llm import llm_client

        tools_by_name = {
            tool.name: tool
            for tool in self.tools
        }

        system = llm_client.build_system_message(
            role_instruction=self.system_prompt,
            extra_context=extra_context,
            output_contract=self.output_contract,
        )

        messages: list = [
            system,
            HumanMessage(content=user_text),
        ]

        llm = llm_client.bind_tools(
            self.tools,
            worker_kind=self.role,
        )

        collected_results: list[dict[str, Any]] = []
        final_text: str | None = None

        for iteration in range(self.max_iterations):
            context.log(
                f"Выполняю поиск: шаг {iteration + 1}.",
                stage="tool_call",
            )

            response = await llm.ainvoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                content = getattr(response, "content", None)

                if isinstance(content, str) and content.strip():
                    final_text = content.strip()

                break

            for tool_call in tool_calls:
                tool_name, tool_args, tool_call_id = _tool_call_parts(
                    tool_call,
                )

                if not tool_name or not tool_call_id:
                    logger.warning(
                        "Malformed tool call from role=%s: %r",
                        self.role,
                        tool_call,
                    )

                    messages.append(
                        ToolMessage(
                            tool_call_id=str(
                                tool_call_id or "unknown"
                            ),
                            content="Ошибка: некорректный вызов инструмента.",
                        )
                    )
                    continue

                tool = tools_by_name.get(tool_name)

                if tool is None:
                    logger.warning(
                        "Unknown tool call from role=%s: %s",
                        self.role,
                        tool_name,
                    )

                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            content=(
                                f"Ошибка: инструмент {tool_name!r} "
                                "недоступен."
                            ),
                        )
                    )
                    continue

                try:
                    result = await tool.ainvoke(
                        {
                            "name": tool_name,
                            "args": tool_args,
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    )

                    content, artifact = _tool_result_parts(result)

                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            content=_serialize_tool_content(content),
                        )
                    )

                    collected_results.extend(
                        _collect_result_items(
                            tool_name=tool_name,
                            content=content,
                            artifact=artifact,
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "Tool failed: role=%s tool=%s",
                        self.role,
                        tool_name,
                    )

                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            content=(
                                "Ошибка выполнения инструмента: "
                                f"{type(exc).__name__}"
                            ),
                        )
                    )

        return ToolLoopResult(
            tool_results=collected_results,
            final_text=final_text,
        )


def _tool_call_parts(
    raw_call: Any,
) -> tuple[str | None, dict[str, Any], str | None]:
    if isinstance(raw_call, dict):
        return (
            raw_call.get("name"),
            raw_call.get("args") or {},
            raw_call.get("id"),
        )

    return (
        getattr(raw_call, "name", None),
        getattr(raw_call, "args", None) or {},
        getattr(raw_call, "id", None),
    )


def _tool_result_parts(
    result: Any,
) -> tuple[Any, Any]:
    if isinstance(result, ToolMessage):
        return result.content, result.artifact

    return result, None


def _serialize_tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value

    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


def _collect_result_items(
    *,
    tool_name: str,
    content: Any,
    artifact: Any,
) -> list[dict[str, Any]]:
    """
    Read-only tools возвращают envelope:

    {
      "entity": "...",
      "mode": "...",
      "result_count": ...,
      "results": [...]
    }

    В Worker payload сохраняем только flat items с явным source tool.
    """
    value = artifact if artifact is not None else content

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []

    if not isinstance(value, dict):
        return []

    entity = value.get("entity")
    mode = value.get("mode")
    results = value.get("results")

    if not isinstance(results, list):
        return []

    output: list[dict[str, Any]] = []

    for result in results:
        if not isinstance(result, dict):
            continue

        output.append(
            {
                **result,
                "_search_entity": entity,
                "_search_mode": mode,
                "_source_tool": tool_name,
            }
        )

    return output