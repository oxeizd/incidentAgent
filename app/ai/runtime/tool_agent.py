"""
app/ai/runtime/tool_agent.py — единственная реализация LLM agent loop.

Wave 1 (ToolLoopResult v2): раньше результат каждого tool call сразу
превращался в плоский список dict (_tag_and_flatten), теряя identity tool
call, arguments и nested artifact contracts (например, SearchPage с
total_count/has_more/display — см. app/memory/search_contracts.py).
SearchPage мог быть ошибочно интерпретирован как обычная запись, если тул
вернул список вместо dict.

Теперь ToolLoopAgent.run() накапливает `ToolExecution` — один per tool call,
с именем, call_id, arguments и НЕТРОНУТЫМ artifact (тем, что вернул
`ToolMessage.artifact`, либо распарсенным JSON из content, если artifact не
задан). `ToolLoopResult.artifacts()` отдаёт эти artifacts как есть — полный
contract сохраняется от tool call до потребителя (см. app/ai/nodes/search.py).

`tool_results` оставлен как temporary compatibility property (Wave 1 migration
rule: убрать только после того, как все потребители перейдут на
`executions`/`artifacts()`). Он воспроизводит СТАРОЕ поведение
_tag_and_flatten: раскрывает list-артефакты в отдельные dict-записи,
одиночный dict оборачивает в список из одного элемента, всё остальное
(строки, числа, None) отбрасывает молча — как было раньше.

ДОБАВЛЕНО (сохранено из предыдущей версии): явные OTEL-span'ы вокруг каждого
вызова тула (tracer из opentelemetry.trace — тот же TracerProvider, что уже
настроен в observability/phoenix.py). Не полагаемся на то, срабатывает ли
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

from langchain_core.messages import HumanMessage, ToolMessage
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


@dataclass(frozen=True)
class ToolExecution:
    """Один вызов тула: identity (name/call_id) + arguments + content + нетронутый artifact."""

    name: str
    call_id: str
    arguments: dict[str, Any]
    content: str
    artifact: Optional[Any]


@dataclass(frozen=True)
class ToolLoopResult:
    executions: tuple[ToolExecution, ...]
    final_text: Optional[str]
    result_tags: dict[str, str] = field(default_factory=dict)

    def executions_for(self, tool_name: str) -> List[ToolExecution]:
        return [e for e in self.executions if e.name == tool_name]

    def artifacts(self, tool_name: Optional[str] = None) -> List[Any]:
        """
        Полные artifacts (SearchPage и т.п.), без flatten. Если для тула
        задан result_tag (см. ToolLoopAgent.result_tags), он подмешивается
        в artifact (в dict — как ключ "kind", в list[dict] — в каждый item),
        сохраняя ту же семантику тегирования, что была в старом
        _tag_and_flatten, но без разрушения структуры (total_count/has_more/
        display остаются на месте у dict-артефактов вида SearchPage).
        """
        out: List[Any] = []
        for execution in self.executions:
            if tool_name is not None and execution.name != tool_name:
                continue
            if execution.artifact is not None:
                out.append(self._tagged(execution))
        return out

    def _tagged(self, execution: ToolExecution) -> Any:
        tag = self.result_tags.get(execution.name)
        artifact = execution.artifact
        if not tag:
            return artifact
        if isinstance(artifact, dict):
            return {**artifact, "kind": tag}
        if isinstance(artifact, list):
            return [{**item, "kind": tag} if isinstance(item, dict) else item for item in artifact]
        return artifact

    @property
    def tool_results(self) -> List[dict]:
        """
        DEPRECATED (temporary compatibility projection, см. докстринг модуля).
        Не использовать в новых nodes — используйте `.executions`/`.artifacts()`.
        Убрать только после того, как все текущие потребители мигрируют.
        """
        flat: List[dict] = []
        for execution in self.executions:
            artifact = self._tagged(execution)
            if isinstance(artifact, list):
                flat.extend(item for item in artifact if isinstance(item, dict))
            elif isinstance(artifact, dict):
                flat.append(artifact)
        return flat


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

        executions: List[ToolExecution] = []
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

                        resolved_artifact = artifact if artifact is not None else _try_parse_json(content)
                        executions.append(ToolExecution(
                            name=name, call_id=tc_id, arguments=args,
                            content=content_str, artifact=resolved_artifact,
                        ))

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

        return ToolLoopResult(executions=tuple(executions), final_text=final_text, result_tags=dict(self.result_tags))
