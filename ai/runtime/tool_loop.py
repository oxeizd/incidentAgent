from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.services.llm import llm_client


class ToolLoopError(RuntimeError):
    """Модель или tool protocol не смогли завершить один agent turn."""


@dataclass(frozen=True)
class ToolLoopResult:
    """Финальный ответ модели и технические результаты tool calls текущего turn."""

    message: AIMessage
    tool_results: list[ToolMessage]


class ToolLoopRunner:
    """
    Универсальный in-memory tool loop одного LLM-вызова.

    Runner не знает о ConversationTask, StepRun, history, planner или memory.
    Вызывающий worker передаёт system message + локальную user/assistant
    переписку, а затем сам решает, что сохранить в StepRun.conversation.

    AIMessage с tool calls и ToolMessage используются только внутри текущего
    вызова. Они не пишутся в checkpoint и не становятся частью локального
    user/assistant диалога агента.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 4,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1.")

        self._max_rounds = max_rounds

    async def run(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[BaseTool],
        worker_kind: str,
    ) -> ToolLoopResult:
        if not messages:
            raise ToolLoopError("Tool loop requires at least one message.")

        tool_by_name = _index_tools(tools)
        current_messages = list(messages)
        tool_results: list[ToolMessage] = []

        for _ in range(self._max_rounds):
            response = await llm_client.ainvoke(
                current_messages,
                tools=list(tools),
                worker_kind=worker_kind,
            )
            message = _require_ai_message(response)

            if not message.tool_calls:
                return ToolLoopResult(
                    message=message,
                    tool_results=tool_results,
                )

            current_messages.append(message)

            round_results = await _run_tool_calls(
                message=message,
                tool_by_name=tool_by_name,
            )
            current_messages.extend(round_results)
            tool_results.extend(round_results)

        raise ToolLoopError(
            f"Tool loop exceeded {self._max_rounds} rounds."
        )


def _index_tools(
    tools: Sequence[BaseTool],
) -> dict[str, BaseTool]:
    indexed: dict[str, BaseTool] = {}

    for tool in tools:
        if tool.name in indexed:
            raise ToolLoopError(f"Duplicate tool name: {tool.name!r}.")

        indexed[tool.name] = tool

    return indexed


async def _run_tool_calls(
    *,
    message: AIMessage,
    tool_by_name: dict[str, BaseTool],
) -> list[ToolMessage]:
    results: list[ToolMessage] = []

    for tool_call in message.tool_calls:
        tool_call_id = tool_call.get("id")
        tool_name = tool_call.get("name")
        tool_args = tool_call.get("args")

        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ToolLoopError("Tool call has no id.")

        if not isinstance(tool_name, str) or not tool_name:
            raise ToolLoopError("Tool call has no name.")

        if not isinstance(tool_args, dict):
            raise ToolLoopError(
                f"Tool call {tool_name!r} has invalid arguments."
            )

        tool = tool_by_name.get(tool_name)
        if tool is None:
            raise ToolLoopError(f"Unknown requested tool: {tool_name!r}.")

        try:
            result = await tool.ainvoke(tool_args)
            content = _tool_result_text(result)
            status = "success"
        except Exception as exc:
            content = _tool_error_text(exc)
            status = "error"

        results.append(
            ToolMessage(
                tool_call_id=tool_call_id,
                name=tool_name,
                status=status,
                content=content,
            )
        )

    return results


def _require_ai_message(response: Any) -> AIMessage:
    if isinstance(response, AIMessage):
        return response

    raise ToolLoopError(
        "LLM client returned a non-AI message in tool loop."
    )


def _tool_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result

    if result is None:
        return "null"

    return str(result)


def _tool_error_text(error: Exception) -> str:
    text = str(error).strip()
    if not text:
        text = error.__class__.__name__

    return f"Tool execution failed: {text}"