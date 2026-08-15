"""
app/ai/runtime/tool_agent.py — единственная реализация LLM agent loop.

ДОБАВЛЕНО: явные OTEL-span'ы вокруг каждого вызова тула (tracer из
opentelemetry.trace — тот же TracerProvider, что уже настроен в
observability/phoenix.py). Не полагаемся на то, срабатывает ли
автоинструментация LangChainInstrumentor для BaseTool.ainvoke — она может
молча не покрывать тулы при рассинхроне версий openinference/langchain-core,
или GigaChat может не до конца заполнять tool_calls-метаданные, из-за
которых span тула остаётся "осиротевшим" в дереве трейса. Явный span
гарантированно виден в Phoenix и содержит имя тула, аргументы и превью
результата — независимо от состояния автоинструментации.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, FrozenSet, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphInterrupt
from opentelemetry import trace as otel_trace

from app.ai.runtime.node_kit import NodeCtx, UserDeviated
from app.services.llm import llm_client

logger = logging.getLogger(__name__)
_tracer = otel_trace.get_tracer(__name__)

_OUTPUT_PREVIEW_LIMIT = 2000


def _tool_call_fields(tc: Any) -> tuple[Optional[str], dict, Optional[str]]:
    if isinstance(tc, dict):
        return tc.get("name"), tc.get("args") or {}, tc.get("id")
    return getattr(tc, "name", None), getattr(tc, "args", None) or {}, getattr(tc, "id", None)


def _try_parse_json(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def _tag_and_flatten(tool_name: str, result: Any, result_tags: dict[str, str]) -> List[dict]:
    tag = result_tags.get(tool_name)

    def _with_tag(item: dict) -> dict:
        return {**item, "kind": tag} if tag else item

    if isinstance(result, list):
        return [_with_tag(item) for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [_with_tag(result)]
    return []


@dataclass
class ToolLoopResult:
    tool_results: List[dict]
    final_text: Optional[str]


@dataclass
class ToolLoopAgent:
    role: str
    system_prompt: str
    tools: List[BaseTool]
    max_iterations: int = 5
    interrupting_tools: FrozenSet[str] = field(default_factory=frozenset)
    result_tags: dict[str, str] = field(default_factory=dict)
    output_contract: Optional[str] = None

    async def run(
        self, ctx: NodeCtx, *, user_text: str, extra_context: Optional[dict] = None,
    ) -> ToolLoopResult:
        tools_by_name = {t.name: t for t in self.tools}
        system = llm_client.build_system_message(
            role_instruction=self.system_prompt, extra_context=extra_context,
            output_contract=self.output_contract,
        )
        messages: list = [system, HumanMessage(content=user_text)]
        llm = llm_client.bind_tools(self.tools, worker_kind=self.role)

        collected: List[dict] = []
        last_response: Any = None

        for _ in range(self.max_iterations):
            response = await llm.ainvoke(messages)
            last_response = response
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                break

            for tc in tool_calls:
                name, args, tc_id = _tool_call_fields(tc)
                if not name or tc_id is None:
                    messages.append(ToolMessage(
                        content="error: malformed tool_call",
                        tool_call_id=str(tc_id or "unknown"),
                    ))
                    continue

                tool_func = tools_by_name.get(name)
                if tool_func is None:
                    messages.append(ToolMessage(content=f"error: unknown tool {name}", tool_call_id=tc_id))
                    continue

                if name in self.interrupting_tools:
                    args = {**args, "worker_id": ctx.worker["worker_id"], "round": ctx.worker["rounds"] + 1}

                call = {"name": name, "args": args, "id": tc_id, "type": "tool_call"}

                with _tracer.start_as_current_span(f"tool.{name}") as span:
                    span.set_attribute("tool.name", name)
                    span.set_attribute("tool.worker_id", ctx.worker["worker_id"])
                    try:
                        span.set_attribute("tool.args", json.dumps(args, ensure_ascii=False, default=str))
                    except Exception:
                        pass

                    try:
                        tool_message = await tool_func.ainvoke(call)
                        content = tool_message.content if isinstance(tool_message, ToolMessage) else tool_message
                        artifact = getattr(tool_message, "artifact", None) if isinstance(tool_message, ToolMessage) else None

                        content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
                        messages.append(ToolMessage(content=content_str, tool_call_id=tc_id))

                        span.set_attribute("tool.content_preview", content_str[:_OUTPUT_PREVIEW_LIMIT])
                        if artifact is not None:
                            try:
                                artifact_str = json.dumps(artifact, ensure_ascii=False, default=str)
                                span.set_attribute("tool.artifact_preview", artifact_str[:_OUTPUT_PREVIEW_LIMIT])
                                span.set_attribute("tool.artifact_count", len(artifact) if isinstance(artifact, list) else 1)
                            except Exception:
                                pass

                        parsed = artifact if artifact is not None else _try_parse_json(content)
                        collected.extend(_tag_and_flatten(name, parsed, self.result_tags))

                    except GraphInterrupt:
                        span.set_attribute("tool.interrupted", True)
                        raise
                    except UserDeviated:
                        span.set_attribute("tool.deviated", True)
                        raise
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_attribute("tool.error", str(exc))
                        logger.error("Тул %s упал: %s", name, exc)
                        messages.append(ToolMessage(content=f"Ошибка: {exc}", tool_call_id=tc_id))

        final_text = None
        if last_response is not None and isinstance(last_response.content, str) and last_response.content.strip():
            final_text = last_response.content

        return ToolLoopResult(tool_results=collected, final_text=final_text)