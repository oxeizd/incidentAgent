"""
app/ai/graph/orchestrator.py

ФИКСА в _on_create_presentation:
  - раньше ссылка на презентацию кладется в текст сообщения как относительный
    API-путь ("/api/v1/threads/.../presentations/...") — в чате это просто
    неработавший текст, не абсолютный URL, никуда не ведёт при клике/копировании.
    теперь current_artifact_id ПЕРЕКЛАДАЕтСЯ на презентацию, и она уходит
    в тот же artifact-канал ответа (public_artifact_view в post_thread_message/
    sse.py), который уже отдаётся клиенту структурированно с каждым ответом —
    тот же механизм, что уже работает для incident_report.
  - Плюс HTML сохраняется на диск сервера (save_presentation_html) как
    надёжный запасной канал — независимо от того, обновился ли фронт/API.

ИСПРАВЛЕНО (сессия рефакторинга):
  - run_worker() вызывал build_subgraphs() НА КАжДОЙ реплике каждого треда —
    четыре StateGraph (search/rca/editor/creator) пересобирались и
    перекомпилировались заново при каждом обращении к серверу, хотя это
    чистая структурная сборка без какого-либо per-request состояния. теперь
    подграфы собираются один раз и кэшируются в модульной переменной.
  - Диспетчеризация "какую finalize-функцию/какой артефакт-хук применить
    для этого worker kind" вынесена в два словаря (_FINALIZERS/_ARTIFACT_HOOKS)
    вместо раскиданных по трём обработчикам inline if kind == "editor"/"rca".

ИСПРАВЛЕНО (метод отправки файла в чат):
  - готовая презентация теперь доставляется и структурированным artifact-
    каналом, и рабочей ссылкой на скачивание (app/api/app.py:download_artifact_file).

ДОБАВЛЕНО (хранилище презентаций — "мои презентации"/"общее хранилище"):
  - Когда презентация готова, её структурные данные (payload.collected —
    единственный источник правды, см. app/ai/nodes/creator.py) сохраняются
    отдельно от artifact-канала через app/memory/repository/presentations.py.
    Артефакт/HTML в чате — по-прежнему то, что видит пользователь сразу;
    запись в presentations — то, что позволяет её найти потом в "моих
    презентациях" и опубликовать в общее хранилище без нового прогона агента.
  - owner_user_id берётся из таблицы threads (get_thread_owner) — не из
    OrchestratorState, чтобы не заводить второй источник владения тредом.
  - Если владелец не найден (совсем маловероятно, только при гонке/ручном
    вызове графа без создания треда через API) — просто не сохраняем в
    presentations, чат и артефакт всё равно работают как раньше.
"""
from __future__ import annotations
from typing import Callable, Literal, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.runtime.factory import spawn_worker, SpawnError
from app.ai.runtime.artifacts import create_artifact, replace_artifact_sections
from app.ai.runtime.presentation_storage import save_presentation_html
from app.ai.graph.build import build_subgraphs
from app.ai.graph.merge import finalize_worker, finalize_editor_result, suspend_focus, resume_focus, defer_worker
from app.ai.registry.intents import INTENT_REGISTRY, register_intent
from app.memory.repository.incidents import get_incident_by_number
from app.memory.repository.threads import get_thread_owner
from app.memory.repository.presentations import create_presentation
from app.services.llm import llm_client
from app.ai.prompts.registry import get_prompt

_SUPERVISOR_FALLBACK_PROMPT = (
    "Классифицируй намерение пользователя по последней реплике. Если интент "
    "предполагает поиск (new_search, search_then_analyze) и реплика — "
    "уточнение предыдущего запроса, заполни resolved_query самодостаточным "
    "перефразом с деталями из истории диалога. edit_report — правка формы/"
    "формулировки готового отчёта или конкретной меры без новых фактов. "
    "reanalyze_report — пользователь сообщает НОВОЙ факт/лог/метрику/"
    "результат отката, способный изменить причину или меры; заполни evidence. "
    "create_presentation — просьба собрать презентацию/слайды, с готовым "
    "отчётом или без него; короткие продолжения ('презентацию', 'без отчёта, "
    "давай') в контексте недавнего разговора про презентацию — тоже "
    "create_presentation, не chitchat и не отдельная новая тема."
)
_EDIT_FALLBACK_PROMPT = (
    "Извлеки из реплики пользователя, какую секцию отчёта редактировать, и инструкцию.\n\n"
    "Если пользователь называет номер вместе с недовольством ('мера N плохая', "
    "'причина N не нравится', 'замени N', 'N не подходит', 'дай другую N') — "
    "это почти всегда про КОНКРЕТНУИЗМенение системную меру из списка tasks (он был "
    "показан пользователю пронумерованным), даже если использовано слово "
    "'причина'/'пункт' вместо 'мера'. В этом случае target_section='tasks', "
    "task_number = ТОТ ЖЕ номер, что назвал пользователь (как в показанном "
    "ему списке, начиная с 1) — не вычисляй индекс сам, просто верни число "
    "как есть.\n\n"
    "target_section='analysis' — только если пользователь явно говорит про "
    "ТЕКСТ (формулировку, раздел, абзац): 'сократи', 'перепиши', 'убери "
    "раздел', без ссылки на номер конкретной меры."
)
_CHITCHAT_FALLBACK_PROMPT = (
    "Ты — ассистент по разбору ИТ-инцидентов. Пользователь написал что-то не "
    "связанное напрямую с задачей (приветствие, благодарность, случайный "
    "вопрос). Ответь кратко и по-человечески, без канцелярита."
)


class IntentClassification(BaseModel):
    intent: Literal[
        "new_search", "search_then_analyze", "analyze",
        "resume_previous", "edit_report", "reanalyze_report",
        "create_presentation",
        "cancel_current", "chitchat_or_other",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    incident_number: str | None = Field(
        None, description="Номер инцидента (при intent=analyze, или create_presentation без готового отчёта).",
    )
    raw_description: str | None = Field(
        None, description="Свободное описание проблемы (при intent=analyze, или create_presentation без готового отчёта).",
    )
    resolved_query: str | None = Field(
        None,
        description=(
            "только для new_search/search_then_analyze: самодостаточный перефраз "
            "последней реплики пользователя с учётом истории диалога."
        ),
    )
    evidence: str | None = Field(
        None,
        description=(
            "только для reanalyze_report: точная формулировка нового факта/лога/"
            "метрики/результата отката, который пользователь только сообщил."
        ),
    )


class EditRequest(BaseModel):
    target_section: str
    instruction: str
    task_number: int | None = Field(
        None,
        description=(
            "Номер меры КАК НАЗВАЛ ПОЛЬЗОВАТЕЛЬ (1-based, ровно как в показанном "
            "ему списке) — только если target_section='tasks'. НЕ вычисляй "
            "0-based индекс сам, просто верни то же число, что в реплике."
        ),
    )


_subgraphs_cache: dict | None = None


def _get_subgraphs() -> dict:
    global _subgraphs_cache
    if _subgraphs_cache is None:
        _subgraphs_cache = build_subgraphs()
    return _subgraphs_cache


_FINALIZERS: dict[str, Callable[[OrchestratorState, dict], dict]] = {
    "editor": finalize_editor_result,
}

_ARTIFACT_HOOKS: dict[str, Callable[[OrchestratorState, dict], dict]] = {}


def _finalize_for(kind: str) -> Callable[[OrchestratorState, dict], dict]:
    return _FINALIZERS.get(kind, finalize_worker)


def _artifact_update_for(kind: str, state: OrchestratorState, worker: dict) -> dict:
    hook = _ARTIFACT_HOOKS.get(kind)
    return hook(state, worker) if hook else {}


async def classify_intent(state: OrchestratorState) -> dict:
    text = state["messages"][-1].content if state["messages"] else ""
    context_hint = ""
    if state["pending_interrupt"] and state["focus_worker_id"]:
        context_hint = (
            f"Агент только что спросил: {state['pending_interrupt']['question']!r}. "
            "Если реплика — ответ на этот вопрос, интент resume_previous."
        )

    recent = state["messages"][-10:] if len(state["messages"]) > 10 else list(state["messages"])
    messages = [
        llm_client.build_system_message(
            role_instruction=get_prompt("supervisor", fallback=_SUPERVISOR_FALLBACK_PROMPT),
            extra_context={"hint": context_hint} if context_hint else None,
            output_contract="JSON по схеме IntentClassification.",
        ),
    ]
    messages.extend(recent)

    result = await llm_client.ainvoke_structured(messages, IntentClassification, worker_kind="supervisor")
    return {
        "intent": result.intent, "intent_confidence": result.confidence,
        "_incident_number": result.incident_number, "_raw_description": result.raw_description,
        "_resolved_query": result.resolved_query, "_evidence": result.evidence,
    }


async def _spawn_and_run(kind: str, input_context: dict, state: OrchestratorState, config, subgraphs):
    spawned = spawn_worker(kind, input_context, state)
    if isinstance(spawned, SpawnError):
        return None, spawned
    result_worker = await subgraphs[kind].ainvoke(spawned.arg, config=config)
    return result_worker, None


def _gate_result_from_payload(payload: dict) -> dict:
    return {
        "root_cause_statement": payload.get("root_cause_statement"),
        "causal_chain": payload.get("causal_chain", []),
        "impact": payload.get("impact", []),
        "symptoms": payload.get("symptoms", []),
        "mitigation": payload.get("mitigation", []),
        "incident_summary": payload.get("incident_summary", ""),
    }


def _format_gate_result_block(gate_result: dict) -> str:
    lines = [
        f"Корневая причина: {gate_result.get('root_cause_statement')}" if gate_result.get("root_cause_statement") else "",
        f"Цепочка 5 почему: {'; '.join(gate_result.get('causal_chain', []))}" if gate_result.get("causal_chain") else "",
        f"Симптомы: {'; '.join(gate_result.get('symptoms', []))}" if gate_result.get("symptoms") else "",
        f"Влияние: {'; '.join(gate_result.get('impact', []))}" if gate_result.get("impact") else "",
    ]
    return "\n".join(line for line in lines if line)


def _create_report_artifact_update(state: OrchestratorState, worker: dict) -> dict:
    if worker["status"] != "done":
        return {}
    summary = worker.get("summary_for_parent") or {}
    if "analysis" not in summary or "tasks" not in summary:
        return {}

    payload = worker.get("payload") or {}
    input_context = worker.get("input_context") or {}
    rca_input = {
        "incident_number": input_context.get("incident_number"),
        "raw_description": input_context.get("raw_description"),
        "search_summary": input_context.get("search_summary"),
        "evidence": input_context.get("evidence", []),
        "gate_result": _gate_result_from_payload(payload),
    }

    artifact_id = f"incident_report-{worker['worker_id']}"
    artifact = create_artifact(
        artifact_id, "incident_report",
        {"analysis": summary["analysis"], "tasks": summary["tasks"], "_rca_input": rca_input},
        created_by_worker_id=worker["worker_id"],
    )
    return {
        "artifacts": {**state["artifacts"], artifact_id: artifact},
        "current_artifact_id": artifact_id,
    }


_ARTIFACT_HOOKS["rca"] = _create_report_artifact_update


async def _reroute_if_deviated(
    result_worker: Optional[dict], state: OrchestratorState, config, subgraphs,
) -> Optional[Command]:
    if not result_worker or result_worker.get("status") != "deviated":
        return None

    deviation_text = result_worker["error"]["message"]
    defer_update = defer_worker(state, result_worker, reason=f"отклонился от вопроса: {deviation_text!r}")

    rerouted_state = {**state, **defer_update, "messages": [*state["messages"], HumanMessage(content=deviation_text)]}
    classified = await classify_intent(rerouted_state)
    rerouted_state = {**rerouted_state, **classified}

    handler = INTENT_REGISTRY.get(classified["intent"], _on_unknown)
    result = await handler(rerouted_state, config, subgraphs)

    merged_update = dict(result.update)
    merged_update["workers"] = {**defer_update["workers"], **merged_update.get("workers", {})}
    merged_update.setdefault("plan_stack", defer_update["plan_stack"])
    merged_update["messages"] = [HumanMessage(content=deviation_text), *merged_update.get("messages", [])]

    return Command(update=merged_update)


async def _handle_edit_report(state: OrchestratorState, config, subgraphs) -> Command:
    artifact_id = state["current_artifact_id"]
    if not artifact_id or artifact_id not in state["artifacts"]:
        return Command(update={"messages": [AIMessage(content="Пока нечего редактировать — сначала нужен готовый отчёт.")]})

    artifact = state["artifacts"][artifact_id]
    sections = artifact["versions"][artifact["current_version"]]["sections"]
    editable_sections = [k for k in sections.keys() if not k.startswith("_")]

    extra_context = {"available_sections": editable_sections}
    if "tasks" in sections:
        extra_context["tasks"] = [
            {"number": i + 1, "title": t.get("title")} for i, t in enumerate(sections["tasks"])
        ]

    system = llm_client.build_system_message(
        role_instruction=get_prompt("editor_intent", fallback=_EDIT_FALLBACK_PROMPT),
        extra_context=extra_context,
        output_contract="JSON по схеме EditRequest.",
    )
    user_text = state["messages"][-1].content
    edit_req = await llm_client.ainvoke_structured([system, HumanMessage(content=user_text)], EditRequest, worker_kind="editor")

    task_index = (edit_req.task_number - 1) if edit_req.task_number else None

    input_context = {
        "payload": {
            "target_artifact_id": artifact_id, "target_section": edit_req.target_section,
            "instruction": edit_req.instruction, "task_index": task_index,
        },
        "artifact_id": artifact_id,
        "current_section_value": sections.get(edit_req.target_section),
    }

    if edit_req.target_section == "tasks":
        rca_input = sections.get("_rca_input") or {}
        input_context["current_tasks"] = sections.get("tasks", [])
        input_context["rca_context_block"] = _format_gate_result_block(rca_input.get("gate_result", {}))

    worker, err = await _spawn_and_run("editor", input_context, state, config, subgraphs)
    if err is not None:
        return Command(update={"messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(worker, state, config, subgraphs)
    if reroute is not None:
        return reroute

    return Command(update=_finalize_for("editor")(state, worker))


async def _handle_direct_rca(state, config, subgraphs, *, incident_number=None, raw_description=None) -> Command:
    input_context = {"payload": {}}
    if incident_number:
        input_context["incident_number"] = incident_number
    elif raw_description:
        input_context["raw_description"] = raw_description
    else:
        return Command(update={"messages": [AIMessage(content="Не понял, что именно анализировать.")]})

    worker, err = await _spawn_and_run("rca", input_context, state, config, subgraphs)
    if err is not None:
        return Command(update={"messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(worker, state, config, subgraphs)
    if reroute is not None:
        return reroute

    update = _finalize_for("rca")(state, worker)
    update.update(_artifact_update_for("rca", state, worker))
    return Command(update=update)


@register_intent("cancel_current")
async def _on_cancel_current(state: OrchestratorState, config, subgraphs) -> Command:
    return Command(update={"messages": [AIMessage(content="хорошо, отменил текущую задачу.")],
                            "focus_worker_id": None, "pending_interrupt": None})


@register_intent("resume_previous")
async def _on_resume_previous(state: OrchestratorState, config, subgraphs) -> Command:
    upd = resume_focus(state)
    if upd is None:
        return Command(update={"messages": [AIMessage(content="Не нашёл, к чему возвращаться.")]})

    worker_id = upd["focus_worker_id"]
    worker = state["workers"].get(worker_id)
    if worker is None:
        return Command(update={**upd, "messages": [AIMessage(content="Не нашёл сохранённый прогресс по этой задаче.")]})

    if worker["status"] != "deviated":
        return Command(update=upd)

    kind = worker["kind"]
    input_context = {**worker["input_context"], "payload": worker["payload"]}
    result_worker, err = await _spawn_and_run(kind, input_context, state, config, subgraphs)
    if err is not None:
        return Command(update={**upd, "messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(result_worker, state, config, subgraphs)
    if reroute is not None:
        return Command(update={**upd, **reroute.update})

    final_update = {**upd, **_finalize_for(kind)(state, result_worker)}
    final_update.update(_artifact_update_for(kind, state, result_worker))
    return Command(update=final_update)


@register_intent("edit_report")
async def _on_edit_report(state: OrchestratorState, config, subgraphs) -> Command:
    return await _handle_edit_report(state, config, subgraphs)


@register_intent("analyze")
async def _on_analyze(state: OrchestratorState, config, subgraphs) -> Command:
    return await _handle_direct_rca(
        state, config, subgraphs,
        incident_number=state.get("_incident_number"),
        raw_description=state.get("_raw_description"),
    )


@register_intent("reanalyze_report")
async def _on_reanalyze_report(state: OrchestratorState, config, subgraphs) -> Command:
    artifact_id = state["current_artifact_id"]
    if not artifact_id or artifact_id not in state["artifacts"]:
        return Command(update={"messages": [AIMessage(content="Сначала нужен готовый RCA-отчёт, чтобы уточнить его новыми фактами.")]})

    artifact = state["artifacts"][artifact_id]
    current_sections = artifact["versions"][artifact["current_version"]]["sections"]
    rca_input = current_sections.get("_rca_input")
    if not rca_input:
        return Command(update={"messages": [AIMessage(content="Не нашёл исходные данные анализа для повторного прогона.")]})

    evidence_text = state.get("_evidence") or state["messages"][-1].content
    new_input_context = {
        "incident_number": rca_input.get("incident_number"),
        "raw_description": rca_input.get("raw_description"),
        "search_summary": rca_input.get("search_summary"),
        "evidence": [*rca_input.get("evidence", []), evidence_text],
    }

    worker, err = await _spawn_and_run("rca", {"payload": {}, **new_input_context}, state, config, subgraphs)
    if err is not None:
        return Command(update={"messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(worker, state, config, subgraphs)
    if reroute is not None:
        return reroute

    update = _finalize_for("rca")(state, worker)

    if worker["status"] == "done":
        summary = worker.get("summary_for_parent") or {}
        if "analysis" in summary and "tasks" in summary:
            new_rca_input = {**new_input_context, "gate_result": _gate_result_from_payload(worker.get("payload") or {})}
            updated_artifact = replace_artifact_sections(
                dict(artifact),
                {"analysis": summary["analysis"], "tasks": summary["tasks"], "_rca_input": new_rca_input},
                produced_by_worker_id=worker["worker_id"],
                note=f"Повторный анализ с учётом нового факта: {evidence_text}",
            )
            update["artifacts"] = {**state["artifacts"], artifact_id: updated_artifact}
            update["current_artifact_id"] = artifact_id

    return Command(update=update)


@register_intent("create_presentation")
async def _on_create_presentation(state: OrchestratorState, config, subgraphs) -> Command:
    artifact_id = state["current_artifact_id"]

    if artifact_id and artifact_id in state["artifacts"]:
        artifact = state["artifacts"][artifact_id]
        sections = artifact["versions"][artifact["current_version"]]["sections"]
        input_context = {"payload": {}, "artifact_id": artifact_id, "artifact_sections": sections}
    else:
        if state.get("_incident_number"):
            incident = await get_incident_by_number(state["_incident_number"])
            source_text = str(incident) if incident else f"Инцидент {state['_incident_number']} не найден в базе."
        elif state.get("_raw_description"):
            source_text = state["_raw_description"]
        else:
            source_text = state["messages"][-1].content
        input_context = {"payload": {}, "source_text": source_text}

    worker, err = await _spawn_and_run("creator", input_context, state, config, subgraphs)
    if err is not None:
        return Command(update={"messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(worker, state, config, subgraphs)
    if reroute is not None:
        return reroute

    update = _finalize_for("creator")(state, worker)

    if worker["status"] == "done":
        summary = worker.get("summary_for_parent") or {}
        if "html" in summary:
            presentation_id = f"presentation-{worker['worker_id']}"
            presentation_artifact = create_artifact(
                presentation_id, "presentation", {"html": summary["html"]},
                created_by_worker_id=worker["worker_id"],
            )
            update["artifacts"] = {**update.get("artifacts", state["artifacts"]), presentation_id: presentation_artifact}
            update["current_artifact_id"] = presentation_id

            local_path = save_presentation_html(state["thread_id"], presentation_id, summary["html"])

            owner_user_id = await get_thread_owner(state["thread_id"])
            db_presentation_id = None
            if owner_user_id:
                collected = (worker.get("payload") or {}).get("collected") or {}
                analysis_markdown = None
                if artifact_id and artifact_id in state["artifacts"]:
                    a = state["artifacts"][artifact_id]
                    analysis_markdown = a["versions"][a["current_version"]]["sections"].get("analysis")
                db_presentation_id = await create_presentation(
                    owner_user_id, state["thread_id"], fields=collected, analysis_markdown=analysis_markdown,
                )

            download_url = f"/api/v1/threads/{state['thread_id']}/artifacts/{presentation_id}/file"
            mine_note = (
                f" сохранена в «Моих презентациях» (id: {db_presentation_id}) — можно править поля "
                f"и опубликовать в общее хранилище без нового запроса ко мне."
                if db_presentation_id else ""
            )
            update["messages"] = [
                *update.get("messages", []),
                AIMessage(content=(
                    f"Презентация готова (см. панель артефакта). "
                    f"[Скачать файл]({download_url}).{mine_note} "
                    f"также сохранена локально: {local_path}"
                )),
            ]

    return Command(update=update)


@register_intent("chitchat_or_other")
async def _on_chitchat_or_other(state: OrchestratorState, config, subgraphs) -> Command:
    response = await llm_client.ainvoke(
        [
            llm_client.build_system_message(role_instruction=get_prompt("chitchat", fallback=_CHITCHAT_FALLBACK_PROMPT)),
            state["messages"][-1],
        ],
        worker_kind="supervisor",
    )
    return Command(update={"messages": [AIMessage(content=response.content)]})


@register_intent("new_search")
@register_intent("search_then_analyze")
async def _on_search(state: OrchestratorState, config, subgraphs) -> Command:
    intent = state["intent"]
    suspend_update = suspend_focus(state, reason=f"user switched to intent={intent}") if state["focus_worker_id"] else {}

    raw_query = state.get("_resolved_query") or state["messages"][-1].content
    input_context = {"payload": {"raw_query": raw_query}}
    result_worker, err = await _spawn_and_run("search", input_context, state, config, subgraphs)
    if err is not None:
        return Command(update={**suspend_update, "messages": [AIMessage(content=err.to_user_message())]})

    reroute = await _reroute_if_deviated(result_worker, state, config, subgraphs)
    if reroute is not None:
        return Command(update={**suspend_update, **reroute.update})

    update = _finalize_for("search")(state, result_worker)

    if intent == "search_then_analyze" and result_worker["status"] == "done":
        merged_state = {**state, **update}
        rca_context = {"payload": {}, "parent_worker_id": result_worker["worker_id"],
                        "search_summary": result_worker["summary_for_parent"]}
        rca_worker, rca_err = await _spawn_and_run("rca", rca_context, merged_state, config, subgraphs)
        if rca_err is None:
            rca_reroute = await _reroute_if_deviated(rca_worker, merged_state, config, subgraphs)
            if rca_reroute is not None:
                return Command(update={**suspend_update, **update, **rca_reroute.update})
            rca_update = _finalize_for("rca")(merged_state, rca_worker)
            update["workers"] = {**update["workers"], **rca_update["workers"]}
            update["pending_interrupt"] = rca_update["pending_interrupt"]
            update["focus_worker_id"] = rca_update["focus_worker_id"]
            if "messages" in rca_update:
                update["messages"] = [*update.get("messages", []), *rca_update["messages"]]
            update.update(_artifact_update_for("rca", merged_state, rca_worker))
        else:
            update.setdefault("messages", []).append(AIMessage(content=rca_err.to_user_message()))

    return Command(update={**suspend_update, **update})


async def _on_unknown(state: OrchestratorState, config, subgraphs) -> Command:
    return Command(update={"messages": [AIMessage(content="Не понял, что нужно сделать.")]})


async def run_worker(state: OrchestratorState, config) -> Command:
    subgraphs = _get_subgraphs()
    handler = INTENT_REGISTRY.get(state["intent"], _on_unknown)
    return await handler(state, config, subgraphs)


def build_orchestrator_graph(checkpointer=None):
    g = StateGraph(OrchestratorState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("run_worker", run_worker)
    g.add_edge(START, "classify_intent")
    g.add_edge("classify_intent", "run_worker")
    g.add_edge("run_worker", END)

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)
