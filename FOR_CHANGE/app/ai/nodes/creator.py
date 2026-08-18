from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeCtx, worker_node
from app.memory.repository.incidents import get_incident_by_number
from app.services.llm import llm_client
from memory.artifacts.presentations.document import (
    PresentationAssignment,
    PresentationDocument,
)

_EXTRACT_EXTRA_FALLBACK_PROMPT = (
    "ты — ассистент. у нас уже есть точные данные о причине и мерах по "
    "инциденту (см. контекст 'known'). из текста анализа извлеки только "
    "оставшиеся оформительские поля презентации: юнит, команду/ФИО "
    "ответственного, краткую суть (1 предложение), этап процесса, оценку "
    "потерь, хронологию событий (список отдельных событий с датой/временем "
    "и описанием, каждое — отдельным элементом списка). если чего-то в "
    "тексте нет — ставь '—' (для хронологии — пустой список)."
)

_UPDATE_FALLBACK_PROMPT = (
    "у нас уже есть частичные данные об инциденте (см. контекст 'current'). "
    "пользователь ответил на уточняющий вопрос текстом. заполни поля, для "
    "которых в ответе есть подходящая информация. одна фраза может закрывать "
    "сразу несколько полей. поля без релевантной информации оставь '—' "
    "(для хронологии — пустой список)."
)

_PLACEHOLDERS = ("", "—", None, [])


class ExtractedIncidentData(BaseModel):
    number: str = Field(
        default="—",
        json_schema_extra={"required_for_completion": True, "label": "номер инцидента"},
    )
    unit: str = "—"
    team: str = "—"
    brief: str = Field(
        default="—",
        json_schema_extra={"required_for_completion": True, "label": "суть инцидента"},
    )
    description: str = Field(
        default="—",
        json_schema_extra={"required_for_completion": True, "label": "описание"},
    )
    cause: str = Field(
        default="—",
        json_schema_extra={"required_for_completion": True, "label": "причину"},
    )
    chain: str = "—"
    stage: str = "—"
    impact: str = Field(
        default="—",
        json_schema_extra={"required_for_completion": True, "label": "влияние"},
    )
    losses: str = "—"
    operational_measures: str = "—"
    systemic_measures: str = "—"
    timeline: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "label": (
                "хронология событий: каждый элемент — одно событие "
                "вида «ДД.MM ЧЧ:ММ — описание»"
            )
        },
    )

    @classmethod
    def required_for_completion(cls) -> dict[str, str]:
        required: dict[str, str] = {}
        for name, field_info in cls.model_fields.items():
            extra = field_info.json_schema_extra
            if isinstance(extra, dict) and extra.get("required_for_completion"):
                required[name] = str(extra.get("label", name))
        return required


class ExtraDesignFields(BaseModel):
    unit: str = "—"
    team: str = "—"
    brief: str = "—"
    stage: str = "—"
    losses: str = "—"
    timeline: list[str] = Field(default_factory=list)


_REQUIRED_FIELD_LABELS = ExtractedIncidentData.required_for_completion()
_REQUIRED_FIELDS = tuple(_REQUIRED_FIELD_LABELS)


def _merge_extracted(current: dict[str, Any], extracted: BaseModel) -> dict[str, Any]:
    """Single merge rule: an extracted placeholder never overwrites a known value."""
    result = dict(current)
    for key, value in extracted.model_dump().items():
        if value not in _PLACEHOLDERS:
            result[key] = value
    return result


async def _ask_llm_for_fields(
    *,
    prompt_role: str,
    fallback_prompt: str,
    schema: type[BaseModel],
    extra_context: dict[str, Any] | None,
    source_text: str,
) -> BaseModel:
    system_message = llm_client.build_system_message(
        role_instruction=get_prompt(prompt_role, fallback=fallback_prompt),
        extra_context=extra_context,
    )
    return await llm_client.ainvoke_structured(
        [system_message, HumanMessage(content=source_text)],
        schema,
        worker_kind="creator",
    )


def _known_block(collected: dict[str, Any]) -> str:
    return "\n".join(
        f"{key}: {value}"
        for key, value in collected.items()
        if value not in _PLACEHOLDERS
    )


def _causal_chain_to_text(causal_chain: list[object], root_cause_statement: str) -> str:
    lines: list[str] = []
    if root_cause_statement:
        lines.append(f"Корневая причина: {root_cause_statement}")

    for step in causal_chain:
        if not isinstance(step, str):
            continue
        text = re.sub(r"^почему\s*\d+\s*:\s*", "", step, flags=re.IGNORECASE).strip()
        if text and text != root_cause_statement:
            lines.append(f"Следствие: {text}")

    return "\n".join(lines)


def _join_strings(items: Any) -> str:
    if not isinstance(items, list):
        return "—"
    joined = "; ".join(item for item in items if isinstance(item, str))
    return joined or "—"


def _assignments_from_tasks(tasks: list[Any]) -> list[PresentationAssignment]:
    """
    Build presentation assignments from validated RCA tasks.

    Status, created_at, deadline_at and responsible are left unset here: they
    belong to the assignments table and will be enriched once that lookup is
    implemented (tracked as a follow-up, not part of this change).
    """
    assignments: list[PresentationAssignment] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue

        assignments.append(
            PresentationAssignment(
                title=str(task.get("title") or "—"),
                description=str(task.get("description") or "—"),
                addresses=str(task.get("addresses") or "—"),
                priority=task.get("priority"),
                type=task.get("type"),
                expected_result=str(task.get("expected_result") or "—"),
            )
        )

    return assignments


def _seed_from_artifact(sections: dict) -> dict[str, Any]:
    rca_input = sections.get("_rca_input")
    rca_input = rca_input if isinstance(rca_input, dict) else {}

    gate_result = rca_input.get("gate_result")
    gate_result = gate_result if isinstance(gate_result, dict) else {}

    root_cause = str(gate_result.get("root_cause_statement") or "")

    return {
        "number": rca_input.get("incident_number") or "—",
        "description": (
            rca_input.get("raw_description")
            or gate_result.get("incident_summary")
            or "—"
        ),
        "cause": root_cause or "—",
        "chain": (
            _causal_chain_to_text(gate_result.get("causal_chain") or [], root_cause)
            or "—"
        ),
        "impact": _join_strings(gate_result.get("impact")),
        "operational_measures": _join_strings(gate_result.get("mitigation")),
    }


@worker_node("collect_fields")
async def collect_fields(ctx: NodeCtx) -> dict[str, Any]:
    payload = ctx.typed.payload
    collected = dict(payload.collected)

    artifact_sections = ctx.worker["input_context"].get("artifact_sections")
    artifact_sections = artifact_sections if isinstance(artifact_sections, dict) else None

    if not collected:
        collected = (
            await _collect_from_artifact(ctx, artifact_sections)
            if artifact_sections is not None
            else await _collect_from_incident_flow(ctx)
        )

    if not payload.reviewed:
        reviewed_collected = await _review_with_user(ctx, collected)
        return ctx.running(
            message="Данные для презентации заполнены. Проверьте их перед сборкой.",
            payload_update={"collected": reviewed_collected, "reviewed": True},
        )

    missing = _missing_required_fields(collected)

    if not missing:
        return ctx.running(
            message="Данные для презентации собраны.",
            payload_update={"collected": collected},
        )

    if ctx.worker["rounds"] >= ctx.worker["max_rounds"]:
        return ctx.finished(
            status="failed",
            message="Не удалось собрать все данные для презентации.",
            payload_update={"collected": collected},
        )

    labels = ", ".join(_REQUIRED_FIELD_LABELS[field] for field in missing)
    question = f"Для презентации не хватает: {labels}. Заполните форму или ответьте текстом."

    answered = await ctx.ask_form(question, ExtractedIncidentData, current=collected)
    collected = await _apply_form_answer(collected, answered)

    return ctx.awaiting(question=question, payload_update={"collected": collected})


@worker_node("build_presentation")
async def build_presentation(ctx: NodeCtx) -> dict[str, Any]:
    payload = ctx.typed.payload

    artifact_sections = ctx.worker["input_context"].get("artifact_sections")
    artifact_sections = artifact_sections if isinstance(artifact_sections, dict) else {}

    ctx.log("Собираю документ презентации.")

    tasks = artifact_sections.get("tasks")
    tasks = tasks if isinstance(tasks, list) else []

    document = PresentationDocument.model_validate(
        {
            **payload.collected,
            "analysis_markdown": str(artifact_sections.get("analysis") or ""),
            "assignments": _assignments_from_tasks(tasks),
        }
    )

    return ctx.done(
        message="Данные презентации собраны.",
        summary={"presentation_document": document.to_storage()},
    )


def route_after_collect(worker: dict) -> str:
    if worker.get("status") in {"failed", "deviated"}:
        return "END"

    payload = worker.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    if not payload.get("reviewed"):
        return "collect_fields"

    collected = payload.get("collected")
    collected = collected if isinstance(collected, dict) else {}

    return "build_presentation" if not _missing_required_fields(collected) else "collect_fields"


async def _collect_from_incident_flow(ctx: NodeCtx) -> dict[str, Any]:
    incident_number = ctx.worker["input_context"].get("incident_number")

    if not isinstance(incident_number, str) or not incident_number.strip():
        incident_number = await ctx.ask(
            "Укажите номер инцидента, для которого нужно собрать презентацию.",
            type="question",
        )

    incident_number = str(incident_number).strip()
    if not incident_number:
        return {"number": "—"}

    ctx.log(f"Ищу инцидент {incident_number!r} в базе.")
    incident = await get_incident_by_number(incident_number)

    if incident is None:
        ctx.log(f"Инцидент {incident_number!r} не найден в базе.")
        return {"number": incident_number}

    reason = str(incident.get("reason_inc") or "").strip()
    seeded: dict[str, Any] = {
        "number": incident_number,
        "team": str(incident.get("work_group") or "").strip() or "—",
        "description": reason or "—",
    }

    return await _fill_extra_design_fields(
        seeded,
        source_text=reason or "(нет описания инцидента)",
    )


async def _collect_from_artifact(ctx: NodeCtx, artifact_sections: dict) -> dict[str, Any]:
    ctx.log("Беру точные данные из готового отчёта, извлекаю оформительские поля.")

    collected = _seed_from_artifact(artifact_sections)
    analysis_text = str(artifact_sections.get("analysis") or "")

    return await _fill_extra_design_fields(
        collected,
        source_text=analysis_text or "(нет текста анализа)",
    )


async def _fill_extra_design_fields(
    collected: dict[str, Any],
    *,
    source_text: str,
) -> dict[str, Any]:
    known = _known_block(collected)

    extra = await _ask_llm_for_fields(
        prompt_role="creator_extract_extra",
        fallback_prompt=_EXTRACT_EXTRA_FALLBACK_PROMPT,
        schema=ExtraDesignFields,
        extra_context={"known": known} if known else None,
        source_text=source_text,
    )
    return _merge_extracted(collected, extra)


async def _review_with_user(ctx: NodeCtx, collected: dict[str, Any]) -> dict[str, Any]:
    ctx.log("Показываю собранные данные пользователю для проверки.")

    answered = await ctx.ask_form(
        "Проверьте данные для презентации и отредактируйте при необходимости.",
        ExtractedIncidentData,
        current=collected,
    )
    return await _apply_form_answer(collected, answered)


async def _apply_form_answer(
    collected: dict[str, Any],
    answered: dict[str, Any],
) -> dict[str, Any]:
    raw_text = answered.pop("_raw_text_fallback", None)

    if isinstance(raw_text, str):
        updates = await _ask_llm_for_fields(
            prompt_role="creator_update",
            fallback_prompt=_UPDATE_FALLBACK_PROMPT,
            schema=ExtractedIncidentData,
            extra_context={"current": collected},
            source_text=raw_text,
        )
        return _merge_extracted(collected, updates)

    merged = dict(collected)
    merged.update({key: value for key, value in answered.items() if value not in _PLACEHOLDERS})
    return merged


def _missing_required_fields(collected: dict) -> list[str]:
    return [field for field in _REQUIRED_FIELDS if collected.get(field) in {None, "", "—"}]