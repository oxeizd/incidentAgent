from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeContext, worker_node
from app.ai.runtime.services import get_memory
from app.memory.artifacts.presentations.document import PresentationDocument
from app.services.llm import llm_client


DASH = "—"


_EXTRACT_FALLBACK_PROMPT = """
Ты собираешь структурированные данные для презентации ИТ-инцидента.

Извлеки только подтверждённые сведения из пользовательского текста.
Не придумывай даты, номера, системы, команды, потери или меры.

Если строковое значение неизвестно, верни "—".
Если timeline неизвестен, верни [].

Номер инцидента — обязательное поле для standalone презентации. Если номер
не указан в тексте, верни "—": node спросит его отдельно.
"""

_ENRICH_FALLBACK_PROMPT = """
Ты дополняешь presentation document данными из incident и RCA.

Точные данные RCA имеют приоритет для:
- cause;
- chain;
- impact;
- operational_measures;
- systemic_measures;
- analysis_markdown;
- assignments.

Данные incident имеют приоритет для:
- number;
- description;
- system/team/unit;
- временных меток и timeline.

Верни только поля, для которых есть подтверждённые данные. Не затирай уже
известные поля значением "—". Для timeline верни список отдельных событий.
"""

_UPDATE_FALLBACK_PROMPT = """
Пользователь дал дополнительную информацию для презентации инцидента.

Обнови только поля, которые явно или надёжно следуют из ответа пользователя.
Не придумывай данные. Неизвестные строки верни как "—", timeline как [].
"""


class PresentationFields(BaseModel):
    """
    Form/extraction schema для presentation document.

    required_for_completion — единый источник truth для creator:
    - проверка обязательных данных;
    - form schema в interrupt;
    - human-readable labels.
    """

    model_config = ConfigDict(extra="forbid")

    number: str = Field(
        default=DASH,
        json_schema_extra={
            "required_for_completion": True,
            "label": "номер инцидента",
        },
    )

    unit: str = Field(default=DASH)
    team: str = Field(default=DASH)

    brief: str = Field(
        default=DASH,
        json_schema_extra={
            "required_for_completion": True,
            "label": "краткая суть инцидента",
        },
    )

    description: str = Field(
        default=DASH,
        json_schema_extra={
            "required_for_completion": True,
            "label": "описание инцидента",
        },
    )

    cause: str = Field(
        default=DASH,
        json_schema_extra={
            "required_for_completion": True,
            "label": "подтверждённая причина",
        },
    )

    chain: str = Field(default=DASH)
    stage: str = Field(default=DASH)

    impact: str = Field(
        default=DASH,
        json_schema_extra={
            "required_for_completion": True,
            "label": "влияние на продукт",
        },
    )

    losses: str = Field(default=DASH)
    operational_measures: str = Field(default=DASH)
    systemic_measures: str = Field(default=DASH)

    timeline: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "label": (
                "хронология событий: каждый элемент — отдельное событие"
            ),
        },
    )

    @classmethod
    def required_field_labels(cls) -> dict[str, str]:
        labels: dict[str, str] = {}

        for name, field_info in cls.model_fields.items():
            extra = field_info.json_schema_extra or {}

            if (
                isinstance(extra, dict)
                and extra.get("required_for_completion")
            ):
                labels[name] = str(
                    extra.get("label", name)
                )

        return labels


class ExtractedPresentationFields(PresentationFields):
    """
    Отдельный semantic alias для LLM structured extraction.
    """


_REQUIRED_FIELD_LABELS = PresentationFields.required_field_labels()
_REQUIRED_FIELDS = tuple(_REQUIRED_FIELD_LABELS)


def _is_empty(value: Any) -> bool:
    return value in (
        None,
        "",
        DASH,
        [],
        {},
        (),
    )


def _non_empty_update(
    base: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """
    Мержит только полезные значения.

    LLM не может стереть подтверждённое поле значением "—" или пустым list.
    """
    result = dict(base)

    for key, value in update.items():
        if not _is_empty(value):
            result[key] = value

    return result


def _missing_required_fields(
    values: dict[str, Any],
) -> list[str]:
    return [
        field_name
        for field_name in _REQUIRED_FIELDS
        if _is_empty(values.get(field_name))
    ]


def _incident_to_fields(
    incident: dict[str, Any],
) -> dict[str, Any]:
    """
    Преобразует LLM-safe incident row в presentation поля.

    Это deterministic mapping, не LLM extraction.
    """
    number = incident.get("number") or DASH
    description = incident.get("description") or DASH
    reason = incident.get("reason_inc") or DASH
    impact = incident.get("impact") or DASH
    solution = incident.get("solution") or DASH

    system_name = incident.get("system_name") or ""
    element_name = incident.get("element_name") or ""
    work_group = incident.get("work_group") or ""
    executor_name = incident.get("executor_name") or ""

    team_parts = [
        str(value).strip()
        for value in (
            work_group,
            executor_name,
        )
        if str(value).strip()
    ]

    timeline: list[str] = []

    if incident.get("start_time"):
        timeline.append(
            f"{incident['start_time']} — начало инцидента"
        )

    if incident.get("end_time"):
        timeline.append(
            f"{incident['end_time']} — окончание инцидента"
        )

    return {
        "number": str(number),
        "unit": str(system_name) if system_name else DASH,
        "team": ", ".join(team_parts) or DASH,
        "brief": str(description) if description else DASH,
        "description": str(description),
        "cause": str(reason),
        "chain": DASH,
        "stage": (
            str(element_name)
            if element_name
            else DASH
        ),
        "impact": str(impact),
        "losses": DASH,
        "operational_measures": str(solution),
        "systemic_measures": DASH,
        "timeline": timeline,
    }


def _tasks_to_assignments(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assignments: list[dict[str, Any]] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue

        assignments.append(
            {
                "title": task.get("title") or DASH,
                "description": task.get("description") or DASH,
                "addresses": task.get("addresses") or DASH,
                "priority": task.get("priority"),
                "type": task.get("type"),
                "status": "new",
                "expected_result": (
                    task.get("expected_result") or DASH
                ),
            }
        )

    return assignments


def _tasks_to_text(
    tasks: list[dict[str, Any]],
) -> str:
    lines: list[str] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue

        title = str(task.get("title") or "").strip()
        description = str(task.get("description") or "").strip()

        if title and description:
            lines.append(f"{title}: {description}")
        elif title:
            lines.append(title)

    return "\n".join(lines) or DASH


def _rca_sections_to_fields(
    sections: dict[str, Any],
) -> dict[str, Any]:
    """
    Маппинг versioned incident_report sections -> presentation fields.

    RCA имеет приоритет для аналитических полей.
    """
    rca_input = sections.get("_rca_input") or {}
    gate_result = rca_input.get("gate_result") or {}
    tasks = sections.get("tasks") or []

    root_cause = gate_result.get("root_cause_statement") or DASH
    causal_chain = gate_result.get("causal_chain") or []
    impact = gate_result.get("impact") or []
    mitigation = gate_result.get("mitigation") or []

    chain_lines = []

    if root_cause != DASH:
        chain_lines.append(f"Корневой: {root_cause}")

    chain_lines.extend(
        f"Следствие: {item}"
        for item in causal_chain
        if item and item != root_cause
    )

    return {
        "number": rca_input.get("incident_number") or DASH,
        "brief": (
            rca_input.get("raw_description")
            or gate_result.get("incident_summary")
            or DASH
        ),
        "description": (
            rca_input.get("raw_description")
            or gate_result.get("incident_summary")
            or DASH
        ),
        "cause": root_cause,
        "chain": "\n".join(chain_lines) or DASH,
        "impact": "; ".join(
            str(value)
            for value in impact
            if str(value).strip()
        ) or DASH,
        "operational_measures": "; ".join(
            str(value)
            for value in mitigation
            if str(value).strip()
        ) or DASH,
        "systemic_measures": _tasks_to_text(tasks),
        "analysis_markdown": sections.get("analysis") or "",
        "assignments": _tasks_to_assignments(tasks),
    }


def _to_presentation_document(
    collected: dict[str, Any],
) -> PresentationDocument:
    """
    Валидирует и нормализует финальный persisted presentation document.
    """
    payload = {
        "number": collected.get("number", DASH),
        "unit": collected.get("unit", DASH),
        "team": collected.get("team", DASH),
        "brief": collected.get("brief", DASH),
        "description": collected.get("description", DASH),
        "cause": collected.get("cause", DASH),
        "chain": collected.get("chain", DASH),
        "stage": collected.get("stage", DASH),
        "impact": collected.get("impact", DASH),
        "losses": collected.get("losses", DASH),
        "operational_measures": collected.get(
            "operational_measures",
            DASH,
        ),
        "systemic_measures": collected.get(
            "systemic_measures",
            DASH,
        ),
        "timeline": collected.get("timeline") or [],
        "analysis_markdown": collected.get(
            "analysis_markdown",
            "",
        ),
        "assignments": collected.get("assignments") or [],
    }

    return PresentationDocument.model_validate(payload)


async def _extract_from_text(
    *,
    source_text: str,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Один LLM structured extraction call для source text.
    """
    prompt_name = (
        "creator_update"
        if current
        else "creator_extract"
    )

    fallback = (
        _UPDATE_FALLBACK_PROMPT
        if current
        else _EXTRACT_FALLBACK_PROMPT
    )

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            prompt_name,
            fallback=fallback,
        ),
        extra_context=(
            {"current": current}
            if current
            else None
        ),
        output_contract=(
            "JSON строго по схеме ExtractedPresentationFields."
        ),
    )

    extracted = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(content=source_text),
        ],
        ExtractedPresentationFields,
        worker_kind="creator",
    )

    return extracted.model_dump()


async def _load_incident_fields(
    incident_number: str,
) -> dict[str, Any]:
    incident = await get_memory().get_incident_for_agent(
        number=incident_number,
    )

    if incident is None:
        return {}

    return _incident_to_fields(incident)


@worker_node("collect_fields")
async def collect_fields(
    ctx: NodeContext,
) -> dict:
    """
    Собирает поля PresentationDocument.

    При повторном входе после form interrupt payload.collected уже содержит
    заполненные пользователем значения. Нода не делает повторный extraction,
    если collected непустой.
    """
    payload = ctx.typed.payload
    input_context = ctx.worker["input_context"]

    collected = dict(payload.collected)

    if not collected:
        ctx.log(
            "Собираю исходные данные для презентации.",
            stage="presentation_collect",
        )

        artifact_sections = input_context.get("artifact_sections")
        incident_number = input_context.get("incident_number")
        source_text = input_context.get("source_text")

        # 1. RCA — источник аналитических полей и системных мер.
        if isinstance(artifact_sections, dict):
            collected = _non_empty_update(
                collected,
                _rca_sections_to_fields(artifact_sections),
            )

            if not incident_number:
                rca_input = artifact_sections.get("_rca_input") or {}
                incident_number = rca_input.get("incident_number")

        # 2. Incident — источник паспорта/описания/timeline.
        if isinstance(incident_number, str) and incident_number.strip():
            incident_fields = await _load_incident_fields(
                incident_number.strip(),
            )
            collected = _non_empty_update(
                collected,
                incident_fields,
            )

            # Номер из explicit input всегда authoritative.
            collected["number"] = incident_number.strip()

        # 3. Standalone свободное описание.
        if source_text and not artifact_sections:
            extracted = await _extract_from_text(
                source_text=str(source_text),
            )
            collected = _non_empty_update(
                collected,
                extracted,
            )

        # 4. RCA analysis может содержать полезные design-поля, но только
        # если они ещё не пришли из incident/RCA structured mapping.
        if isinstance(artifact_sections, dict):
            analysis_text = str(
                artifact_sections.get("analysis") or ""
            ).strip()

            if analysis_text:
                extracted = await _extract_from_text(
                    source_text=analysis_text,
                    current=collected,
                )
                collected = _non_empty_update(
                    collected,
                    extracted,
                )

    missing = _missing_required_fields(collected)

    if not missing:
        return ctx.running(
            message="Данные для презентации собраны.",
            payload_update={
                "collected": collected,
            },
        )

    if ctx.worker["rounds"] >= ctx.worker["max_rounds"]:
        labels = ", ".join(
            _REQUIRED_FIELD_LABELS[field_name]
            for field_name in missing
        )

        return ctx.finished(
            status="failed",
            message=(
                "Не удалось собрать обязательные поля презентации: "
                f"{labels}."
            ),
            payload_update={
                "collected": collected,
            },
        )

    labels = ", ".join(
        _REQUIRED_FIELD_LABELS[field_name]
        for field_name in missing
    )

    question = (
        "Для создания презентации не хватает данных: "
        f"{labels}. Заполните форму или ответьте текстом."
    )

    response = await ctx.ask_form(
        question,
        PresentationFields,
        current=collected,
        metadata={
            "purpose": "presentation_fields",
            "missing_required_fields": missing,
        },
    )

    raw_text = response.pop("_raw_text_fallback", None)

    if raw_text is not None:
        ctx.log(
            "Получил текстовый ответ, извлекаю поля презентации.",
            stage="presentation_extract",
        )

        extracted = await _extract_from_text(
            source_text=str(raw_text),
            current=collected,
        )

        collected = _non_empty_update(
            collected,
            extracted,
        )
    else:
        collected = _non_empty_update(
            collected,
            response,
        )

    return ctx.running(
        message="Проверяю заполненные поля презентации.",
        payload_update={
            "collected": collected,
        },
    )


def route_after_collect(
    worker: dict,
) -> str:
    if worker["status"] in {
        "failed",
        "cancelled",
        "deviated",
    }:
        return "END"

    collected = (
        worker.get("payload", {})
        .get("collected", {})
    )

    missing = _missing_required_fields(collected)

    if missing:
        return "collect_fields"

    return "build_presentation"


@worker_node("build_presentation")
async def build_presentation(
    ctx: NodeContext,
) -> dict:
    """
    Валидирует финальный PresentationDocument.

    Не рендерит HTML и не пишет в БД: это сделает orchestrator через
    MemoryFacade после successful worker completion.
    """
    payload = ctx.typed.payload

    ctx.log(
        "Формирую структурированный документ презентации.",
        stage="presentation_document",
    )

    document = _to_presentation_document(
        payload.collected,
    )

    return ctx.done(
        message="Данные презентации собраны.",
        summary={
            "document": document.model_dump(mode="json"),
        },
        payload_update={
            "collected": document.model_dump(mode="json"),
        },
    )