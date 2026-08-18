from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage

from app.ai.nodes.rca import AnalyzerTask
from app.ai.nodes.task_validation import validate_tasks
from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeContext, worker_node
from app.services.llm import llm_client


_EDITOR_FALLBACK_PROMPT = """
Ты редактируешь готовый RCA-отчёт по инструкции пользователя.

Тебе дано текущее содержимое одной секции отчёта и инструкция пользователя.
Верни только новое содержимое секции.

Правила:
- сохраняй фактическую точность;
- не придумывай новые логи, метрики, даты и результаты;
- не меняй другие секции;
- если пользователь просит улучшить формулировку, сохрани исходный смысл;
- не добавляй пояснения о своей работе.
"""

_TASK_REWRITE_FALLBACK_PROMPT = """
Ты редактируешь одну системную меру в RCA-отчёте.

Тебе даны:
- подтверждённая root cause;
- текущая мера;
- инструкция пользователя.

Верни одну новую системную меру в JSON-схеме AnalyzerTask.

Новая мера должна устранять ту же root cause или указанную пользователем
часть причины. Не возвращай общие пожелания вроде «усилить контроль» без
конкретного механизма и ожидаемого результата.
"""


DESTRUCTIVE_KEYWORDS = frozenset(
    {
        "удали",
        "удалить",
        "убери",
        "убрать",
        "очисти",
        "очистить",
        "сотри",
        "стереть",
    }
)


def requires_confirmation(
    instruction: str,
) -> bool:
    normalized = instruction.lower()

    return any(
        keyword in normalized
        for keyword in DESTRUCTIVE_KEYWORDS
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _rewrite_text_section(
    *,
    current_value: Any,
    instruction: str,
) -> str:
    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "editor",
            fallback=_EDITOR_FALLBACK_PROMPT,
        ),
        output_contract=(
            "Верни только новый текст секции. "
            "Без markdown-обёртки, без пояснений."
        ),
    )

    prompt = (
        "Текущее содержимое секции:\n"
        f"{current_value}\n\n"
        "Инструкция пользователя:\n"
        f"{instruction}"
    )

    response = await llm_client.ainvoke(
        [
            system,
            HumanMessage(content=prompt),
        ],
        worker_kind="editor",
    )

    text = str(response.content or "").strip()

    if not text:
        raise ValueError(
            "LLM returned empty edited section"
        )

    return text


def _format_rca_context(
    rca_context: dict[str, Any],
) -> str:
    gate_result = rca_context.get("gate_result") or {}

    lines = [
        (
            f"Корневая причина: "
            f"{gate_result.get('root_cause_statement')}"
            if gate_result.get("root_cause_statement")
            else ""
        ),
        (
            "Цепочка причинности:\n"
            + "\n".join(
                f"- {item}"
                for item in gate_result.get("causal_chain") or []
            )
            if gate_result.get("causal_chain")
            else ""
        ),
        (
            "Симптомы:\n"
            + "\n".join(
                f"- {item}"
                for item in gate_result.get("symptoms") or []
            )
            if gate_result.get("symptoms")
            else ""
        ),
        (
            "Влияние:\n"
            + "\n".join(
                f"- {item}"
                for item in gate_result.get("impact") or []
            )
            if gate_result.get("impact")
            else ""
        ),
    ]

    return "\n".join(
        line
        for line in lines
        if line
    ) or "Контекст root cause отсутствует."


async def _rewrite_task(
    *,
    current_task: dict[str, Any],
    instruction: str,
    rca_context: dict[str, Any],
) -> dict[str, Any]:
    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "rca_task_rewrite",
            fallback=_TASK_REWRITE_FALLBACK_PROMPT,
        ),
        extra_context={
            "root_cause_context": _format_rca_context(
                rca_context
            ),
            "current_task": current_task,
        },
        output_contract="JSON строго по схеме AnalyzerTask.",
    )

    rewritten = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=instruction),
        ],
        AnalyzerTask,
        worker_kind="editor",
    )

    validated = await validate_tasks(
        [rewritten.model_dump()]
    )

    if not validated:
        raise RuntimeError(
            "Task validation returned no task"
        )

    return validated[0]


@worker_node("apply_edit")
async def apply_edit(
    ctx: NodeContext,
) -> dict:
    """
    Изменяет только готовый incident_report artifact.

    Важно:
    - evidence/new facts не обрабатываются здесь;
    - новый факт -> intent reanalyze_report -> повторный RCA;
    - editor делает только controlled artifact edit.
    """
    payload = ctx.typed.payload
    input_context = ctx.worker["input_context"]

    artifact_id = payload.target_artifact_id
    target_section = payload.target_section
    instruction = payload.instruction

    ctx.log(
        f"Применяю правку к секции {target_section}.",
        stage="editor",
    )

    if requires_confirmation(instruction):
        confirmed = await ctx.confirm(
            (
                f"Инструкция может удалить или очистить данные "
                f"в секции «{target_section}»: {instruction!r}. "
                "Подтверждаете изменение?"
            ),
            metadata={
                "purpose": "destructive_report_edit",
                "artifact_id": artifact_id,
                "section": target_section,
            },
        )

        if not confirmed:
            return ctx.cancelled(
                message="Хорошо, правку не применяю."
            )

    if target_section == "tasks":
        return await _apply_task_edit(ctx)

    return await _apply_analysis_edit(ctx)


async def _apply_analysis_edit(
    ctx: NodeContext,
) -> dict:
    payload = ctx.typed.payload
    input_context = ctx.worker["input_context"]

    artifact_id = payload.target_artifact_id
    current_value = input_context.get(
        "current_section_value",
        "",
    )

    new_value = await _rewrite_text_section(
        current_value=current_value,
        instruction=payload.instruction,
    )

    patch = {
        "section": "analysis",
        "original_value": current_value,
        "new_value": new_value,
        "operation": "update",
        "applied_by_worker_id": ctx.worker["worker_id"],
        "timestamp": utc_now_iso(),
        "note": payload.instruction,
    }

    return ctx.done(
        message=(
            "Обновил секцию «analysis»:\n\n"
            f"{new_value}"
        ),
        summary={
            "artifact_id": artifact_id,
            "patch": patch,
        },
        payload_update={
            "proposed_diff": patch,
            "applied_patches": [
                *payload.applied_patches,
                patch,
            ],
        },
    )


async def _apply_task_edit(
    ctx: NodeContext,
) -> dict:
    payload = ctx.typed.payload
    input_context = ctx.worker["input_context"]

    artifact_id = payload.target_artifact_id
    task_index = payload.task_index
    tasks = list(input_context.get("current_tasks") or [])

    if task_index is None:
        return ctx.failed(
            code="missing_task_index",
            message=(
                "Не указан номер системной меры, которую нужно изменить."
            ),
        )

    if task_index < 0 or task_index >= len(tasks):
        return ctx.failed(
            code="invalid_task_index",
            message="Не нашёл указанную системную меру.",
        )

    old_task = tasks[task_index]

    if not isinstance(old_task, dict):
        return ctx.failed(
            code="invalid_task_data",
            message="Системная мера имеет некорректный формат.",
        )

    rca_context = input_context.get("rca_input") or {}

    new_task = await _rewrite_task(
        current_task=old_task,
        instruction=payload.instruction,
        rca_context=rca_context,
    )

    tasks[task_index] = new_task

    return ctx.done(
        message=(
            f"Заменил системную меру {task_index + 1}:\n\n"
            f"**{new_task['title']}** "
            f"[{new_task['priority']}/{new_task['type']}]\n\n"
            f"{new_task['description']}\n\n"
            f"_Устраняет: {new_task['addresses']}_\n\n"
            f"_Ожидаемый результат: "
            f"{new_task['expected_result']}_"
        ),
        summary={
            "artifact_id": artifact_id,
            "updated_tasks": tasks,
            "task_index": task_index,
        },
        payload_update={
            "proposed_diff": {
                "section": "tasks",
                "task_index": task_index,
                "original_task": old_task,
                "new_task": new_task,
            },
        },
    )