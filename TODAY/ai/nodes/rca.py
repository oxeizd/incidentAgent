from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.nodes.task_validation import validate_tasks
from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeContext, worker_node
from app.ai.runtime.services import get_memory
from app.services.llm import llm_client


_GATE_FALLBACK_PROMPT = """
Ты — RCA gate для ИТ-инцидентов.

Оцени, достаточно ли подтверждённых фактов, чтобы сформулировать root cause.

Если причина подтверждена фактами:
- root_cause_present=true;
- root_cause_statement обязателен;
- causal_chain содержит причинно-следственные шаги;
- не выдавай предположение за установленный факт.

Если данных недостаточно:
- root_cause_present=false;
- перечисли missing_information;
- задай до трёх кратких clarifying_questions;
- suspected_root_cause допускается только как рабочая гипотеза.

Не придумывай логи, метрики, таймлайны или действия команд.
"""

_ANALYZER_FALLBACK_PROMPT = """
Ты — ведущий аналитик RCA по ИТ-инцидентам.

На основании подтверждённой root cause сформируй:
1. краткий фактологический RCA-анализ;
2. до пяти конкретных системных мер.

Каждая мера должна устранять конкретную часть причины, а не быть общим
советом вроде «улучшить мониторинг» без механизма, owner и результата.

Не выдумывай недоступные факты. Различай временные mitigation и системные
меры, устраняющие root cause.
"""


class RCAGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "root_cause_present",
        "insufficient_info",
        "contradictory_or_unclear",
        "no_incident_data",
    ]

    reason: str = Field(min_length=1)

    incident_summary: str = ""
    impact: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    mitigation: list[str] = Field(default_factory=list)

    suspected_root_cause: str | None = None
    root_cause_present: bool

    root_cause_statement: str | None = None
    causal_chain: list[str] = Field(default_factory=list)
    evidence_found: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reason: str = ""

    @model_validator(mode="after")
    def validate_consistency(self) -> "RCAGateResponse":
        if self.root_cause_present and not self.root_cause_statement:
            raise ValueError(
                "root_cause_statement is required when "
                "root_cause_present=true"
            )

        if (
            not self.root_cause_present
            and not self.clarifying_questions
            and self.status != "no_incident_data"
        ):
            raise ValueError(
                "clarifying_questions are required when root cause "
                "is not established"
            )

        return self


class AnalyzerTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=500)
    description: str = Field(min_length=3, max_length=5_000)
    addresses: str = Field(
        min_length=3,
        description="Какая часть root cause устраняется.",
    )

    type: Literal[
        "architecture",
        "config",
        "process",
        "monitoring",
        "automation",
    ]

    priority: Literal[
        "high",
        "medium",
        "low",
    ]

    expected_result: str = Field(min_length=3, max_length=2_000)


class AnalyzerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: str = Field(min_length=1)
    tasks: list[AnalyzerTask] = Field(
        default_factory=list,
        max_length=5,
    )


async def _resolve_rca_context(
    input_context: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Строит исходный RCA context в порядке приоритета:

    1. search_summary — search → rca;
    2. incident_number — точная загрузка incident из MemoryFacade;
    3. raw_description — RCA по свободному описанию.

    evidence приклеивается поверх любого основного источника.
    """
    context: dict[str, Any] = {}

    search_summary = input_context.get("search_summary")

    if isinstance(search_summary, dict):
        context = {
            "source": "search_summary",
            "search": search_summary,
        }
    elif input_context.get("incident_number"):
        number = str(input_context["incident_number"]).strip()

        incident = await get_memory().get_incident_for_agent(
            number=number,
        )

        context = {
            "source": "incident_number",
            "incident_number": number,
            "incident": incident,
        }
    elif input_context.get("raw_description"):
        context = {
            "source": "raw_description",
            "description": str(
                input_context["raw_description"]
            ).strip(),
        }

    evidence = input_context.get("evidence")

    if isinstance(evidence, list):
        normalized_evidence = [
            str(item).strip()
            for item in evidence
            if str(item).strip()
        ]

        if normalized_evidence:
            context["additional_evidence"] = normalized_evidence

    return context or None


def _format_rca_context(
    *,
    context: dict[str, Any] | None,
    user_answers: dict[str, str],
) -> str:
    parts: list[str] = []

    if context is None:
        parts.append("Исходные данные об инциденте отсутствуют.")
    elif context.get("source") == "search_summary":
        search = context.get("search") or {}
        results = search.get("results") or []

        parts.append(
            "Результаты поиска:\n"
            f"Запрос: {search.get('query', '—')}\n"
            f"Всего найдено: {search.get('result_count', 0)}"
        )

        for index, item in enumerate(results, start=1):
            parts.append(
                f"Результат {index}:\n"
                f"{_format_mapping(item)}"
            )

    elif context.get("source") == "incident_number":
        incident_number = context.get("incident_number")
        incident = context.get("incident")

        if incident is None:
            parts.append(
                f"Инцидент {incident_number!r} не найден в базе."
            )
        else:
            parts.append(
                f"Инцидент {incident_number}:\n"
                f"{_format_mapping(incident)}"
            )

    elif context.get("source") == "raw_description":
        parts.append(
            "Описание пользователя:\n"
            f"{context.get('description') or '—'}"
        )

    evidence = (
        context.get("additional_evidence")
        if context
        else None
    )

    if evidence:
        parts.append(
            "Дополнительные факты, сообщённые пользователем:\n"
            + "\n".join(f"- {item}" for item in evidence)
        )

    if user_answers:
        parts.append(
            "Ответы пользователя на уточняющие вопросы:\n"
            + "\n".join(
                f"- {question}: {answer}"
                for question, answer in user_answers.items()
            )
        )

    return "\n\n".join(parts)


def _format_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)

    lines: list[str] = []

    for key, item in value.items():
        if key.startswith("_"):
            continue

        if item in (None, "", [], {}, "—"):
            continue

        lines.append(f"{key}: {item}")

    return "\n".join(lines) or "—"


def _format_gate_context(payload: Any) -> str:
    parts = [
        (
            f"Корневая причина: {payload.root_cause_statement}"
            if payload.root_cause_statement
            else ""
        ),
        (
            "Цепочка причинности:\n"
            + "\n".join(
                f"- {item}"
                for item in payload.causal_chain
            )
            if payload.causal_chain
            else ""
        ),
        (
            "Симптомы:\n"
            + "\n".join(
                f"- {item}"
                for item in payload.symptoms
            )
            if payload.symptoms
            else ""
        ),
        (
            "Влияние:\n"
            + "\n".join(
                f"- {item}"
                for item in payload.impact
            )
            if payload.impact
            else ""
        ),
        (
            "Временные меры:\n"
            + "\n".join(
                f"- {item}"
                for item in payload.mitigation
            )
            if payload.mitigation
            else ""
        ),
        (
            f"Резюме инцидента: {payload.incident_summary}"
            if payload.incident_summary
            else ""
        ),
    ]

    return "\n\n".join(
        part
        for part in parts
        if part
    )


def _render_rca_result(
    *,
    analysis: str,
    tasks: list[dict[str, Any]],
) -> str:
    lines = [
        analysis.strip(),
        "",
        "---",
        "",
        "## Системные меры",
    ]

    if not tasks:
        lines.append("Системные меры не сформированы.")
        return "\n".join(lines)

    labels = {
        "NEW": "🆕 Новая",
        "PARTIAL": "⚠️ Частичное совпадение",
        "DUPLICATE": "♻️ Дубликат",
    }

    counts = {
        "NEW": 0,
        "PARTIAL": 0,
        "DUPLICATE": 0,
    }

    for index, task in enumerate(tasks, start=1):
        status = str(task.get("validation_status") or "NEW")
        counts[status] = counts.get(status, 0) + 1

        lines.extend(
            [
                "",
                (
                    f"**{index}. {task.get('title', 'Без названия')}** "
                    f"[{task.get('priority', '—')}/"
                    f"{task.get('type', '—')}] — "
                    f"{labels.get(status, status)}"
                ),
                str(task.get("description") or "—"),
                (
                    f"_Устраняет: "
                    f"{task.get('addresses') or '—'}_"
                ),
                (
                    f"_Ожидаемый результат: "
                    f"{task.get('expected_result') or '—'}_"
                ),
            ]
        )

        if status != "NEW":
            lines.append(
                f"> {task.get('validation_reason') or '—'}"
            )

    lines.extend(
        [
            "",
            (
                f"**Итого:** новых — {counts['NEW']}, "
                f"частичных — {counts['PARTIAL']}, "
                f"дублей — {counts['DUPLICATE']}."
            ),
        ]
    )

    return "\n".join(lines)


@worker_node("rca_gate")
async def rca_gate(
    ctx: NodeContext,
) -> dict:
    """
    Проверяет, хватает ли фактов для RCA.

    При недостатке данных делает native interrupt. При resume LangGraph
    replay-safe повторно строит context и снова вызывает LLM gate; до
    interrupt нет side effects.
    """
    payload = ctx.typed.payload

    ctx.log(
        "Проверяю, достаточно ли фактов для определения root cause.",
        stage="rca_gate",
    )

    context = await _resolve_rca_context(
        ctx.worker["input_context"],
    )

    source_text = _format_rca_context(
        context=context,
        user_answers=payload.user_answers,
    )

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "rca_gate",
            fallback=_GATE_FALLBACK_PROMPT,
        ),
        output_contract="JSON строго по схеме RCAGateResponse.",
    )

    result = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=source_text),
        ],
        RCAGateResponse,
        worker_kind="rca",
    )

    payload_update = {
        "gate_status": result.status,
        "incident_summary": result.incident_summary,
        "impact": result.impact,
        "symptoms": result.symptoms,
        "mitigation": result.mitigation,
        "suspected_root_cause": result.suspected_root_cause,
        "root_cause_present": result.root_cause_present,
        "root_cause_statement": result.root_cause_statement,
        "causal_chain": result.causal_chain,
        "evidence_found": result.evidence_found,
        "missing_information": result.missing_information,
        "confidence": result.confidence,
        "confidence_reason": result.confidence_reason,
    }

    if result.root_cause_present:
        return ctx.running(
            message="Корневая причина подтверждена, формирую RCA.",
            payload_update=payload_update,
        )

    if ctx.worker["rounds"] >= ctx.worker["max_rounds"]:
        return ctx.finished(
            status="failed",
            message=(
                "Не удалось собрать достаточно фактов для RCA "
                "за отведённое число уточнений."
            ),
            payload_update=payload_update,
        )

    questions = [
        question.strip()
        for question in result.clarifying_questions
        if question and question.strip()
    ][:3]

    question_text = (
        " ".join(questions)
        if questions
        else "Уточните, пожалуйста, детали инцидента."
    )

    answer = await ctx.ask(
        question_text,
        metadata={
            "purpose": "rca_clarification",
            "missing_information": result.missing_information,
        },
    )

    return ctx.running(
        message="Уточнение получено, повторно проверяю RCA-контекст.",
        payload_update={
            **payload_update,
            "user_answers": {
                **payload.user_answers,
                question_text: str(answer),
            },
        },
    )


def route_after_gate(
    worker: dict,
) -> str:
    if worker["status"] in {
        "failed",
        "cancelled",
        "deviated",
    }:
        return "END"

    if worker["payload"].get("root_cause_present"):
        return "analyzer"

    return "rca_gate"


@worker_node("analyzer")
async def analyzer(
    ctx: NodeContext,
) -> dict:
    """
    Строит RCA analysis и proposed systemic tasks только после gate.
    """
    payload = ctx.typed.payload

    if not payload.root_cause_present:
        return ctx.failed(
            code="root_cause_not_confirmed",
            message=(
                "Нельзя сформировать RCA: корневая причина "
                "ещё не подтверждена."
            ),
        )

    ctx.log(
        "Формирую RCA-анализ и системные меры.",
        stage="rca_analysis",
    )

    context_text = _format_gate_context(payload)

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "rca_analyzer",
            fallback=_ANALYZER_FALLBACK_PROMPT,
        ),
        output_contract="JSON строго по схеме AnalyzerResult.",
    )

    result = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=context_text),
        ],
        AnalyzerResult,
        worker_kind="rca",
    )

    return ctx.running(
        message="RCA-анализ и системные меры сформированы.",
        payload_update={
            "analysis": result.analysis,
            "tasks": [
                task.model_dump()
                for task in result.tasks
            ],
        },
    )


@worker_node("task_validator")
async def task_validator(
    ctx: NodeContext,
) -> dict:
    """
    Проверяет системные меры на похожие существующие поручения.
    """
    payload = ctx.typed.payload

    if not payload.analysis.strip():
        return ctx.failed(
            code="missing_analysis",
            message="Не найден сформированный RCA-анализ.",
        )

    ctx.log(
        f"Проверяю {len(payload.tasks)} системных мер на дубли.",
        stage="task_validation",
    )

    validated_tasks = await validate_tasks(
        payload.tasks,
    )

    rendered = _render_rca_result(
        analysis=payload.analysis,
        tasks=validated_tasks,
    )

    return ctx.done(
        message=rendered,
        summary={
            "analysis": payload.analysis,
            "tasks": validated_tasks,
        },
        payload_update={
            "validated_tasks": validated_tasks,
        },
    )