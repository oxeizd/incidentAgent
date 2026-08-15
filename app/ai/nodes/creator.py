"""
app/ai/nodes/creator/node.py

Оба пути (с готовым RCA-артефактом и "с нуля") идут через один и тот же
цикл: точные структурные поля (number/cause/chain/impact/меры) — из
RCA-артефакта напрямую; недостающие дизайн-поля (unit/team/brief/stage/
losses/timeline) — одним LLM-вызовом из текста анализа (как старый
EXTRACT_PROMPT делал для всего текста разом); дальше — цикл проверки
REQUIRED и дозапроса, идентичный оригинальному worker_creator.

рефактор 1: _REQUIRED_FIELDS/_FIELD_LABELS раньше были отдельным хардкод-
списком, продублированным вручную из полей ExtractedIncidentData — теперь
это метаданные самих полей схемы (required_for_completion), см.
ExtractedIncidentData.required_for_completion().

рефактор 2: дозапрос недостающих полей теперь идёт через ctx.ask_form() —
клиенту отдаётся структурированная форма (обязательные/опциональные поля,
уже известные значения предзаполнены) вместо одной свободнотекстовой
реплики + повторного LLM-разбора. Для клиентов без поддержки форм
остаётся старый путь: ctx.ask_form() возвращает "_raw_text_fallback", и он
разбирается тем же LLM-промптом, что и раньше — обратная совместимость
не ломается, форма — предпочитаемый путь, а не единственный.

ExtractedIncidentData.timeline — list[str] (список событий хронологии), не
единый текстовый блок — см. app/ai/runtime/form_schema.py (type="array" +
items.type выводятся из аннотации автоматически).

ИСПРАВЛЕНО (хранилище презентаций): _build_full_html/_collected_to_incident_dict
были приватными (префикс "_") — только для внутреннего использования этим
модулем. теперь презентация не существует тОЛько как готовый HTML-артефакт —
структурные данные (collected) сохраняются отдельно в
app/memory/repository/presentations.py ("мои презентации"/"общее хранилище"),
а HTML генерируется по требованию для скачивания (см.
app/api/app.py:presentation_file). Обеим сторонам (build_presentation() здесь
и /api/v1/presentations/{id}/file там) нужны ОДНИ И ТЕ ЖЕ функции сборки —
поэтому build_full_html/collected_to_incident_dict сделаны публичными
(без "_"), а не продублированы в app.py.
"""
from __future__ import annotations
import html as html_module
import re
from datetime import datetime
from typing import Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.ai.prompts.registry import get_prompt
from app.ai.runtime.node_kit import NodeCtx, worker_node
from app.ai.nodes.presentation import (
    CSS, JS, build_single_summary, build_incident_tab, esc,
)
from app.services.llm import llm_client

_EXTRACT_FALLBACK_PROMPT = (
    "ты — ассистент. извлеки из текста данные для презентации инцидента. "
    "если данных нет — ставь '—' в соответствующее поле (для хронологии "
    "events — пустой список, если событий не указано)."
)
_EXTRACT_EXTRA_FALLBACK_PROMPT = (
    "ты — ассистент. у нас уже есть точные данные о причине и мерах по "
    "инциденту (см. контекст 'known'). из текста анализа извлеки тОЛько "
    "оставшиеся оформительские поля презентации: юнит, команду/ФиО "
    "ответственного, краткую суть (1 предложение), этап процесса, оценку "
    "потерь, хронологию событий (список отдельных событий с датой/временем "
    "и описанием, каждое — отдельным элементом списка). если чего-то в "
    "тексте нет — ставь '—' (для хронологии — пустой список)."
)
_UPDATE_FALLBACK_PROMPT = (
    "у нас уже есть частичные данные об инциденте (см. контекст 'current'). "
    "пользователь ответил на уточняющий вопрос текстом (клиент без формы). "
    "заполни поля, для которых в ответе есть подходящая информация — даже "
    "если явной отдельной фразы под конкретное поле нет: одна фраза может "
    "закрывать сразу несколько полей (например, описание причины позволяет "
    "вывести и cause, и краткую суть brief). формулируй производно, не жди точной "
    "цитаты. Поля без релевантной информации оставь '—' (для хронологии — "
    "пустой список, если не упомянута)."
)


class ExtractedIncidentData(BaseModel):
    """
    Схема оформительских/структурных полей презентации инцидента.

    required_for_completion=True в json_schema_extra — поле, без которого
    collect_fields не отпустит воркера дальше (цикл ctx.ask_form()). ута же
    метадата используется build_fields_schema() (app/ai/runtime/form_schema.py)
    для построения JSON-описания формы, отдаваемого клиенту в interrupt-
    payload — один источник правды и для валидации, и для контракта формы.
    Остальные поля опциональны: если данных нет, остаётся '—' и презентация
    просто показывает это как пустое место в верстке (см. presentation.py) —
    форма не требует их заполнения (value=None, но необязательно).

    timeline — единственное поле-список: каждый элемент — одно событие
    хронологии (см. докстринг модуля). Пустой список = "данных нет", как
    "—" для строковых полей (build_fields_schema это учитывает).
    """

    number: str = Field("—", json_schema_extra={"required_for_completion": True, "label": "номер инцидента"})
    unit: str = "—"
    team: str = "—"
    brief: str = Field("—", json_schema_extra={"required_for_completion": True, "label": "суть инцидента"})
    description: str = Field("—", json_schema_extra={"required_for_completion": True, "label": "описание"})
    cause: str = Field("—", json_schema_extra={"required_for_completion": True, "label": "причину"})
    chain: str = "—"
    stage: str = "—"
    impact: str = Field("—", json_schema_extra={"required_for_completion": True, "label": "влияние"})
    losses: str = "—"
    operational_measures: str = "—"
    systemic_measures: str = "—"
    timeline: list[str] = Field(
        default_factory=list,
        json_schema_extra={"label": 'хронология событий (каждый элемент — одно событие вида "ДД.MM ЧЧ:ММ – описание"'},
    )

    @classmethod
    def required_for_completion(cls) -> dict[str, str]:
        """
        {имя_поля: человекочитаемая_метка} — единственный источник правды
        о том, какие поля обязательны для завершения сбора данных.
        """
        out: dict[str, str] = {}
        for name, field_info in cls.model_fields.items():
            extra = field_info.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("required_for_completion"):
                out[name] = extra.get("label", name)
        return out


class ExtraDesignFields(BaseModel):
    """только оформительские поля, которых нет в структурных данных RCA."""
    unit: str = "—"
    team: str = "—"
    brief: str = "—"
    stage: str = "—"
    losses: str = "—"
    timeline: list[str] = Field(default_factory=list)


_REQUIRED_FIELD_LABELS = ExtractedIncidentData.required_for_completion()
_REQUIRED_FIELDS = list(_REQUIRED_FIELD_LABELS.keys())


# --- маппинг в старый формат данных (1-18) --------------------------------

def collected_to_incident_dict(collected: dict) -> dict:
    """
    та же логика, что была в старом _to_incident_dict, с ОДНИМ отличием:
    timeline теперь список в collected (см. ExtractedIncidentData.timeline)
    и склеивается здесь в одну строку с переводами строк — presentation-
    слой (build_incident_tab/build_timeline_items) как и раньше ожидает единый
    текстовый блок. список существует только на этапе сбора данных
    (форма/LLM), а не во всём остальном пайплайне.

    Публичная (без "_") — используется и здесь (build_presentation), и в
    app/api/app.py:presentation_file для генерации HTML по требованию из
    сохранённых в БД fields (см. app/memory/repository/presentations.py).
    """
    timeline_value = collected.get("timeline")
    timeline_text = "\n".join(timeline_value) if isinstance(timeline_value, list) else (timeline_value or "—")

    mapping = {
        1: collected.get("unit", "—"),
        2: collected.get("team", "—"),
        3: collected.get("number", "—"),
        4: collected.get("brief", "—"),
        5: collected.get("description", "—"),
        6: collected.get("cause", "—"),
        7: collected.get("chain", "—"),
        8: collected.get("stage", "—"),
        9: collected.get("impact", "—"),
        10: collected.get("losses", "—"),
        16: collected.get("operational_measures", "—"),
        17: collected.get("systemic_measures", "—"),
        18: timeline_text or "—",
    }
    result = {i: "—" for i in range(1, 19)}
    result.update(mapping)
    return result


def _causal_chain_to_old_format(causal_chain: list, root_cause_statement: str) -> str:
    lines = []
    if root_cause_statement:
        lines.append(f"Корневой: {root_cause_statement}")
    for step in causal_chain or []:
        text = re.sub(r"^почему\s*\d+\s*:\s*", "", step, flags=re.IGNORECASE).strip()
        if text and text != root_cause_statement:
            lines.append(f"Следствие: {text}")
    return "\n".join(lines)


def _tasks_to_measures_text(tasks: list) -> str:
    lines = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        desc = (t.get("description") or "").strip()
        line = f"{title}: {desc}" if desc else title
        if line:
            lines.append(line)
    return "\n".join(lines)


def _seed_from_artifact(sections: dict) -> dict:
    """
    точные структурные поля из RCA-артефакта — number/cause/chain/impact/
    меры/description. Надёжнее, чем LLM-угадывание из текста, поэтому
    заполняются напрямую, без экстракции.
    """
    rca_input = sections.get("_rca_input") or {}
    gate = rca_input.get("gate_result") or {}
    tasks = sections.get("tasks") or []

    return {
        "number": rca_input.get("incident_number") or "—",
        "description": rca_input.get("raw_description") or gate.get("incident_summary") or "—",
        "cause": gate.get("root_cause_statement") or "—",
        "chain": _causal_chain_to_old_format(gate.get("causal_chain", []), gate.get("root_cause_statement", "")) or "—",
        "impact": "; ".join(gate.get("impact", [])) or "—",
        "operational_measures": "; ".join(gate.get("mitigation", [])) or "—",
        "systemic_measures": _tasks_to_measures_text(tasks) or "—",
    }


# --- сборка HTML (вкладка "Анализ" добавляется здесь, не в presentation.py) ---

def _render_analysis_panel(analysis_markdown: str) -> str:
    return (
        '\n<div class="tab-panel" id="tab-analysis">\n'
        '  <div class="detail-card" data-section="full-analysis">'
        '<div class="detail-row"><div class="detail-title editable-text" contenteditable="false">Полный анализ</div>'
        f'<div class="detail-text editable-text" contenteditable="false">{esc(analysis_markdown)}</div></div></div>\n'
        '</div>'
    )


def build_full_html(data: dict, commission_date: str, analysis_markdown: str = "") -> str:
    """Публичная (без "_") — см. докстринг модуля и collected_to_incident_dict()."""
    number = data.get(3, "—")
    safe_num = html_module.escape(str(number), quote=True)
    tab_label = f"EVE-{esc(number)}" if not str(number).upper().startswith("EVE-") else esc(number)
    commission_date_esc = esc(commission_date)

    tab_buttons = '<button class="tab-btn active" data-tab="tab-summary">Содержание</button>'
    tab_buttons += f'<button class="tab-btn" data-tab="tab-{safe_num}">{tab_label}</button>'
    tab_panels = build_single_summary(data) + build_incident_tab(data, root=None)

    if analysis_markdown and analysis_markdown.strip():
        tab_buttons += '<button class="tab-btn" data-tab="tab-analysis">Анализ</button>'
        tab_panels += _render_analysis_panel(analysis_markdown)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Комиссия по инцидентам — {commission_date_esc}</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="app">
    <div class="viewport">
      <div class="shell">
        <div class="header">
          <div class="header-left">
            <div class="header-tribe">Трайб Риски Розничного Бизнеса | {commission_date_esc}</div>
            <h1 class="header-title">Комиссия по инцидентам</h1>
          </div>
          <div class="header-right">
            <div class="tabs-wrapper">
              <button class="tab-nav-btn left" title="Прокрутить влево"></button>
              <div class="tabs">{tab_buttons}</div>
              <button class="tab-nav-btn right" title="Прокрутить вправо"></button>
            </div>
          </div>
        </div>
        <div class="tab-panels">{tab_panels}</div>
      </div>
    </div>
  </div>
  <div class="edit-toolbar">
    <button id="editToggle" class="edit-btn">Ред.</button>
    <button id="restoreBtn" class="edit-btn" style="display:none;">↺</button>
    <button id="saveBtn" class="edit-btn" style="display:none;">✓</button>
  </div>
  <script>{JS}</script>
</body>
</html>"""


# --- ноды ------------------------------------------------------------------

@worker_node("collect_fields")
async def collect_fields(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    collected = dict(payload.collected)
    artifact_sections = ctx.worker["input_context"].get("artifact_sections")

    if not collected:
        if artifact_sections:
            ctx.log("беру точные данные из готового отчёта, извлекаю оформительские поля")
            collected = _seed_from_artifact(artifact_sections)

            analysis_text = artifact_sections.get("analysis", "")
            known_block = "\n".join(f"{k}: {v}" for k, v in collected.items() if v and v != "—")
            system = llm_client.build_system_message(
                role_instruction=get_prompt("creator_extract_extra", fallback=_EXTRACT_EXTRA_FALLBACK_PROMPT),
                extra_context={"known": known_block},
            )
            extra = await llm_client.ainvoke_structured(
                [system, HumanMessage(content=analysis_text or "(нет текста анализа)")],
                ExtraDesignFields, worker_kind="creator",
            )
            for k, v in extra.model_dump().items():
                if v and v != "—":
                    collected[k] = v
        else:
            ctx.log("извлекаю данные для презентации из текста")
            source_text = ctx.worker["input_context"].get("source_text") or "(нет исходного текста)"
            system = llm_client.build_system_message(role_instruction=get_prompt("creator_extract", fallback=_EXTRACT_FALLBACK_PROMPT))
            result = await llm_client.ainvoke_structured(
                [system, HumanMessage(content=source_text)], ExtractedIncidentData, worker_kind="creator",
            )
            collected = result.model_dump()

    missing = [f for f in _REQUIRED_FIELDS if collected.get(f, "—") in ("—", "")]
    if not missing:
        return ctx.running(message="Данные для презентации собраны.", payload_update={"collected": collected})

    if ctx.worker["rounds"] >= ctx.worker["max_rounds"]:
        return ctx.finished(
            status="failed", message="Не удалось собрать все данные для презентации.",
            payload_update={"collected": collected},
        )

    labels = ", ".join(_REQUIRED_FIELD_LABELS.get(f, f) for f in missing)
    question = f"для презентации не хватает: {labels}. заполните форму или ответьте текстом."

    answered = await ctx.ask_form(question, ExtractedIncidentData, current=collected)

    raw_text_fallback = answered.pop("_raw_text_fallback", None)
    if raw_text_fallback is not None:
        ctx.log("клиент без поддержки форм — разбираю свободный текст LLM'ом")
        update_system = llm_client.build_system_message(
            role_instruction=get_prompt("creator_update", fallback=_UPDATE_FALLBACK_PROMPT),
            extra_context={"current": collected},
        )
        updates = await llm_client.ainvoke_structured(
            [update_system, HumanMessage(content=raw_text_fallback)], ExtractedIncidentData, worker_kind="creator",
        )
        for k, v in updates.model_dump().items():
            if v and v != "—":
                collected[k] = v
    else:
        collected.update(answered)

    return ctx.awaiting(question=question, payload_update={"collected": collected})


def route_after_collect(worker) -> str:
    if worker["status"] in ("failed", "deviated"):
        return "END"
    collected = worker["payload"].get("collected", {})
    missing = [f for f in _REQUIRED_FIELDS if collected.get(f, "—") in ("—", "")]
    if not missing:
        return "build_presentation"
    return "collect_fields"


@worker_node("build_presentation")
async def build_presentation(ctx: NodeCtx) -> dict:
    payload = ctx.typed.payload
    artifact_sections = ctx.worker["input_context"].get("artifact_sections")

    ctx.log("собираю презентацию")

    data = collected_to_incident_dict(payload.collected)
    analysis_text = artifact_sections.get("analysis", "") if artifact_sections else ""

    commission_date = datetime.now().strftime("%d.%m.%Y")
    html = build_full_html(data, commission_date, analysis_markdown=analysis_text)

    return ctx.done(
        message="Презентация собрана.",
        summary={"html": html},
        payload_update={"html": html},
    )
