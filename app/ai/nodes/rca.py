"""
app/ai/nodes/rca/node.py

- task_validator теперь использует общий task_validation.validate_tasks
  (переиспользуется editor-нодой для замены одной задачи).
- _resolve_rca_context/_format_incident_context понимают input_context["evidence"]
  (список доп. фактов) — нужно для reanalyze: старый контекст + новые улики
  передаются в gate заново.
"""
from __future__ import annotations

from typing import Optional, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, model_validator

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeCtx, worker_node
from app.ai.nodes.task_validation import validate_tasks
from app.memory.repository.incidents import get_incident_by_number
from app.services.llm import llm_client

_GATE_FALLBACK_PROMPT = (
    "Ты — RCA Incident Analyzer. Оцени, достаточно ли данных для root cause. "
    "Если нет — задай уточняющие вопросы, не угадывай причину."
)
_ANALYZER_FALLBACK_PROMPT = (
    "Ты — ведущий аналитик по разбору инцидентов. Дай краткий фактологический "
    "анализ и до 5 конкретных системных мер, устраняющих корневую причину."
)


class RCAGateResponse(BaseModel):
    status: Literal["root_cause_present", "insufficient_info", "contradictory_or_unclear", "no_incident_data"]
    reason: str
    incident_summary: str = ""
    impact: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    mitigation: list[str] = Field(default_factory=list)
    suspected_root_cause: Optional[str] = Field(
        None, description="Заполняй ТОЛЬКО когда root_cause_present=false — рабочая гипотеза без подтверждения.",
    )
    root_cause_present: bool
    root_cause_statement: Optional[str] = Field(
        None, description="Заполняй ТОЛЬКО когда root_cause_present=true — подтверждённая причина с evidence.",
    )
    causal_chain: list[str] = Field(default_factory=list, description="Шаги 'почему N: <причинно-следственная связь>'.")
    evidence_found: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reason: str

    @model_validator(mode="after")
    def _consistency(self) -> "RCAGateResponse":
        if self.root_cause_present and not self.root_cause_statement:
            self.status = "insufficient_info"
            self.reason = "root_cause_present=true без root_cause_statement — откат до insufficient_info."
            self.root_cause_present = False
            self.root_cause_statement = None
        return self


class AnalyzerTask(BaseModel):
    title: str
    description: str
    addresses: str
    type: Literal["architecture", "config", "process", "monitoring", "automation"]
    priority: Literal["high", "medium", "low"]
    expected_result: str


class AnalyzerResult(BaseModel):
    analysis: str
    tasks: list[AnalyzerTask] = Field(default_factory=list)


# --- вспомогательные функции --------------------------------------------

async def _resolve_rca_context(input_context: dict) -> Optional[dict]:
    if input_context.get("search_summary"):
        context = dict(input_context["search_summary"])
    elif input_context.get("incident_number"):
        incident = await get_incident_by_number(input_context["incident_number"])
        context = {"source": "incident_lookup", "incident": incident}
    elif input_context.get("raw_description"):
        context = {"source": "user_description", "description": input_context["raw_description"]}
    else:
        context = {}

    evidence = input_context.get("evidence")
    if evidence:
        context["additional_evidence"] = list(evidence)

    return context or None


def _format_incident_context(context: Optional[dict], user_answers: dict[str, str]) -> str:
    parts = []
    if context:
        base = {k: v for k, v in context.items() if k != "additional_evidence"}
        if "results" in base:
            count = base.get("result_count", len(base["results"]))
            parts.append(f"Найдено инцидентов: {count}")
            for i, item in enumerate(base["results"], 1):
                parts.append(f"{i}. {item}")
        elif "incident" in base:
            parts.append(f"Инцидент:\n{base['incident']}")
        elif "description" in base:
            parts.append(f"Описание проблемы:\n{base['description']}")
        elif base:
            parts.append(f"Данные инцидента:\n{base}")

        if context.get("additional_evidence"):
            ev_block = "\n".join(f"- {e}" for e in context["additional_evidence"])
            parts.append(f"Дополнительные подтверждённые факты (сообщены пользователем позже):\n{ev_block}")

    if user_answers:
        answers_block = "\n".join(f"- {q}: {a}" for q, a in user_answers.items())
        parts.append(f"Ответы пользователя на уточняющие вопросы:\n{answers_block}")

    return "\n\n".join(parts) if parts else "(нет данных об инциденте)"


def _format_gate_context(payload) -> str:
    lines = [
        f"Корневая причина: {payload.root_cause_statement}" if payload.root_cause_statement else "",
        f"Цепочка 5 почему: {'; '.join(payload.causal_chain)}" if payload.causal_chain else "",
        f"Симптомы: {'; '.join(payload.symptoms)}" if payload.symptoms else "",
        f"Влияние: {'; '.join(payload.impact)}" if payload.impact else "",
        f"Временные меры: {'; '.join(payload.mitigation)}" if payload.mitigation else "",
        f"Резюме: {payload.incident_summary}" if payload.incident_summary else "",
    ]
    return "\n".join(line for line in lines if line)


def _render_result(analysis: str, validated_tasks: list[dict]) -> str:
    if not validated_tasks:
        return analysis.strip() + "\n\n> Нет системных мер для валидации."

    labels = {"NEW": "🆕 Новое", "PARTIAL": "⚠️ Частичное совпадение", "DUPLICATE": "♻️ Дубликат"}
    counts = {"NEW": 0, "PARTIAL": 0, "DUPLICATE": 0}
    parts = [analysis.strip(), "\n---\n## Системные меры и поручения\n"]

    for i, vt in enumerate(validated_tasks, 1):
        counts[vt["validation_status"]] = counts.get(vt["validation_status"], 0) + 1
        parts.append(f"**{i}. {vt['title']}** [{vt['priority']}/{vt['type']}] — {labels[vt['validation_status']]}")
        parts.append(vt["description"])
        if vt.get("addresses"):
            parts.append(f"_Устраняет: {vt['addresses']}_")
        parts.append(f"_Ожидаемый результат: {vt['expected_result']}_")
        if vt["validation_status"] != "NEW":
            parts.append(f"> {vt['validation_reason']}")
        parts.append("")

    parts.append(f"**Итого:** новых — {counts['NEW']}, частичных — {counts['PARTIAL']}, дублей — {counts['DUPLICATE']}.")
    return "\n".join(parts)


# --- ноды -----------------------------------------------------------------

@worker_node("rca_gate")
async def rca_gate(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    ctx.log("Проверяю, достаточно ли данных для root cause")

    context = await _resolve_rca_context(ctx.worker["input_context"])
    incident_text = _format_incident_context(context, payload.user_answers)

    system = llm_client.build_system_message(role_instruction=get_prompt("rca_gate", fallback=_GATE_FALLBACK_PROMPT))
    result = await llm_client.ainvoke_structured(
        [system, HumanMessage(content=incident_text)], RCAGateResponse, worker_kind="rca",
    )

    payload_update = {
        "gate_status": result.status, "incident_summary": result.incident_summary,
        "impact": result.impact, "symptoms": result.symptoms, "mitigation": result.mitigation,
        "suspected_root_cause": result.suspected_root_cause, "root_cause_present": result.root_cause_present,
        "root_cause_statement": result.root_cause_statement, "causal_chain": result.causal_chain,
        "evidence_found": result.evidence_found, "missing_information": result.missing_information,
        "confidence": result.confidence, "confidence_reason": result.confidence_reason,
    }

    if result.root_cause_present:
        ctx.log("Корневая причина установлена, перехожу к анализу")
        return ctx.running(message="Корневая причина установлена.", payload_update=payload_update)

    if ctx.worker["rounds"] >= ctx.worker["max_rounds"]:
        return ctx.finished(
            status="failed",
            message="Не удалось собрать достаточно данных для анализа за отведённое число попыток.",
            payload_update=payload_update,
        )

    questions = result.clarifying_questions or ["Уточните, пожалуйста, детали инцидента."]
    question_text = " ".join(questions[:3])
    # ИСПРАВЛЕНО: раньше был kind="question" — расходилось с переименованным
    # в app/ai/runtime/node_kit.py параметром ctx.ask(type=...).
    answer = await ctx.ask(question_text, type="question")
    payload_update["user_answers"] = {**payload.user_answers, question_text: answer}
    return ctx.awaiting(question=question_text, payload_update=payload_update)


def route_after_gate(worker) -> str:
    if worker["status"] in ("failed", "deviated"):
        return "END"
    if worker["payload"].get("root_cause_present"):
        return "analyzer"
    return "rca_gate"


@worker_node("analyzer")
async def analyzer(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    ctx.log("Формирую структурированный анализ и системные меры")

    existing_measures = ctx.worker["input_context"].get("existing_measures", "")
    context_block = _format_gate_context(payload)
    user_text = context_block + (f"\n\nМероприятия:\n{existing_measures}" if existing_measures else "")

    system = llm_client.build_system_message(role_instruction=get_prompt("rca_analyzer", fallback=_ANALYZER_FALLBACK_PROMPT))
    result = await llm_client.ainvoke_structured(
        [system, HumanMessage(content=user_text)], AnalyzerResult, worker_kind="rca",
    )

    return ctx.running(
        message="Анализ и системные меры сформированы.",
        payload_update={"analysis": result.analysis, "tasks": [t.model_dump() for t in result.tasks]},
    )


@worker_node("task_validator")
async def task_validator(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload

    if not payload.tasks:
        return ctx.done(
            message=_render_result(payload.analysis, []),
            summary={"analysis": payload.analysis, "tasks": []},
        )

    ctx.log(f"Проверяю {len(payload.tasks)} мер(ы) на дубли в базе поручений")
    validated_tasks = await validate_tasks(payload.tasks)

    return ctx.done(
        message=_render_result(payload.analysis, validated_tasks),
        summary={"analysis": payload.analysis, "tasks": validated_tasks},
        payload_update={"validated_tasks": validated_tasks},
    )
