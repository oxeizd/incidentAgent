"""
app/ai/nodes/editor/node.py

ФИКСЫ:
  - _rewrite_text_section теперь показывает НОВЫЙ ТЕКСТ секции в message,
    а не только "Обновил секцию X." — раньше результат приходилось
    перезапрашивать отдельно.
  - _replace_task показывает полную новую меру (не только заголовок).
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.messages import HumanMessage

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeCtx, worker_node
from app.ai.nodes.rca import AnalyzerTask
from app.ai.nodes.task_validation import validate_tasks
from app.services.llm import llm_client

DESTRUCTIVE_KEYWORDS = ("удали", "убери всё", "очисти")
_EDITOR_FALLBACK_PROMPT = "Ты редактируешь секцию отчёта об инциденте по инструкции пользователя."
_TASK_REWRITE_FALLBACK_PROMPT = (
    "Ты — аналитик системных мер по инциденту. Пользователю не подошла "
    "существующая мера. Тебе даны контекст корневой причины, сама неудачная "
    "мера и инструкция пользователя. Верни НОВУЮ меру, устраняющую ТУ ЖЕ "
    "причину (то же 'addresses'), учитывая инструкцию."
)


def requires_confirmation(instruction: str) -> bool:
    return any(kw in instruction.lower() for kw in DESTRUCTIVE_KEYWORDS)


async def _apply_instruction(current_value, instruction: str) -> str:
    system = llm_client.build_system_message(
        role_instruction=get_prompt("editor", fallback=_EDITOR_FALLBACK_PROMPT),
        output_contract="Верни ТОЛЬКО новый текст секции, без пояснений и markdown-обёртки.",
    )
    prompt = f"Текущее содержимое секции:\n{current_value}\n\nИнструкция от пользователя: {instruction}"
    response = await llm_client.ainvoke([system, HumanMessage(content=prompt)], worker_kind="editor")
    return response.content


async def _rewrite_text_section(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    instruction = payload.instruction
    section = payload.target_section
    current_value = ctx.worker["input_context"].get("current_section_value")

    if requires_confirmation(instruction):
        confirmed = await ctx.confirm(f"Это разрушающее изменение секции «{section}»: «{instruction}». Подтвердить?")
        if not confirmed:
            return ctx.cancelled(message="Хорошо, правку не применяю.")

    new_value = await _apply_instruction(current_value, instruction)
    patch_dict = {
        "section": section, "original_value": current_value, "new_value": new_value,
        "operation": "update", "applied_by_worker_id": ctx.worker["worker_id"],
        "timestamp": datetime.now().isoformat(), "note": instruction,
    }

    return ctx.done(
        # ФИКС: раньше было f"Обновил секцию «{section}»." без самого текста.
        message=f"Обновил секцию «{section}»:\n\n{new_value}",
        summary={"artifact_id": ctx.worker["input_context"].get("artifact_id"), "patch": patch_dict},
        payload_update={
            "proposed_diff": patch_dict,
            "applied_patches": [*payload.applied_patches, patch_dict],
        },
    )


async def _replace_task(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    tasks = list(ctx.worker["input_context"].get("current_tasks") or [])
    index = payload.task_index

    if index is None or not (0 <= index < len(tasks)):
        return ctx.failed(code="invalid_task_index", message="Не нашёл указанную системную меру для замены.")

    old_task = tasks[index]
    rca_context_block = ctx.worker["input_context"].get("rca_context_block", "")

    system = llm_client.build_system_message(
        role_instruction=get_prompt("rca_task_rewrite", fallback=_TASK_REWRITE_FALLBACK_PROMPT),
        extra_context={"root_cause_context": rca_context_block, "current_task": old_task},
        output_contract="JSON по схеме новой системной меры.",
    )
    new_task = await llm_client.ainvoke_structured(
        [system, HumanMessage(content=payload.instruction)], AnalyzerTask, worker_kind="editor",
    )

    validated = await validate_tasks([new_task.model_dump()])
    tasks[index] = validated[0]
    new = validated[0]

    return ctx.done(
        # ФИКС: раньше показывался только заголовок новой меры.
        message=(
            f"Заменил меру «{old_task.get('title', '?')}» на:\n\n"
            f"**{new['title']}** [{new['priority']}/{new['type']}]\n"
            f"{new['description']}\n"
            f"_Устраняет: {new.get('addresses', '')}_\n"
            f"_Ожидаемый результат: {new['expected_result']}_"
        ),
        summary={"artifact_id": ctx.worker["input_context"].get("artifact_id"), "updated_tasks": tasks},
        payload_update={"proposed_diff": {"tasks": tasks}},
    )


@worker_node("apply_edit")
async def apply_edit(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    ctx.log(f"Применяю правку к секции {payload.target_section!r}: {payload.instruction!r}")

    if payload.target_section == "tasks" and payload.task_index is not None:
        return await _replace_task(ctx)
    return await _rewrite_text_section(ctx)