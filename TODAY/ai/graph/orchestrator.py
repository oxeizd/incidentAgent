from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from app.ai.graph.build import build_subgraphs
from app.ai.graph.merge import (
    create_rca_report_update,
    defer_worker,
    finalize_editor_result,
    finalize_worker,
    replace_rca_report_update,
    resume_focus,
    suspend_focus,
)
from app.ai.prompts.registry import get_prompt
from app.ai.registry.intents import INTENT_REGISTRY, register_intent
from app.ai.runtime.factory import SpawnError, spawn_worker
from app.ai.runtime.services import get_memory
from app.ai.schemas.orchestrator import OrchestratorState
from app.memory.artifacts.presentations.document import PresentationDocument
from app.services.llm import llm_client


_SUPERVISOR_FALLBACK_PROMPT = """
Ты — supervisor ассистента по ИТ-инцидентам.

Классифицируй последнюю реплику пользователя с учётом истории.

Интенты:

new_search:
- пользователь хочет только найти инциденты/поручения/похожие случаи.

search_then_analyze:
- сначала найти данные, затем сделать RCA;
- например «найди похожие и проанализируй».

analyze:
- сделать RCA по номеру инцидента или свободному описанию;
- RCA может работать без search.

edit_report:
- правка готового RCA-отчёта или конкретной системной меры;
- не используется, если пользователь сообщил новый факт, способный изменить
  root cause, analysis или measures.

reanalyze_report:
- пользователь сообщает новый лог, метрику, факт, результат отката,
  изменение поведения или иной evidence, который может изменить RCA;
- обязательно заполни evidence точной формулировкой нового факта.

create_presentation:
- пользователь просит создать презентацию/слайды;
- если есть готовый RCA report, используй его;
- иначе передай incident_number или raw_description при наличии.

resume_previous:
- пользователь отвечает на только что заданный вопрос.

cancel_current:
- пользователь явно отменяет текущую задачу.

chitchat_or_other:
- приветствие, благодарность или другой запрос вне сценариев.
"""

_EDIT_FALLBACK_PROMPT = """
Извлеки edit request из реплики пользователя.

target_section:
- "tasks" — пользователь просит изменить, заменить или улучшить конкретную
  системную меру;
- "analysis" — пользователь просит изменить текст RCA-анализа.

task_number:
- обязателен только для tasks;
- это номер меры ровно как назвал пользователь, начиная с 1;
- не вычисляй индекс сам, просто верни указанное число.
"""


_CHITCHAT_FALLBACK_PROMPT = """
Ты — ассистент по анализу ИТ-инцидентов.
Ответь кратко, понятно и по-человечески.
"""


class IntentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "new_search",
        "search_then_analyze",
        "analyze",
        "resume_previous",
        "edit_report",
        "reanalyze_report",
        "create_presentation",
        "cancel_current",
        "chitchat_or_other",
    ]

    confidence: float = Field(ge=0.0, le=1.0)

    incident_number: str | None = None
    raw_description: str | None = None
    resolved_query: str | None = None
    evidence: str | None = None


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_section: Literal["analysis", "tasks"]
    instruction: str = Field(min_length=1)
    task_number: int | None = Field(default=None, ge=1)


_subgraphs_cache: dict[str, Any] | None = None


def _get_subgraphs() -> dict[str, Any]:
    global _subgraphs_cache

    if _subgraphs_cache is None:
        _subgraphs_cache = build_subgraphs()

    return _subgraphs_cache


async def classify_intent(
    state: OrchestratorState,
) -> dict[str, Any]:
    """
    Planner запускается на каждый новый user turn.

    Если есть pending_interrupt, LLM получает явный hint, что короткий ответ
    может быть resume_previous.
    """
    context_hint = ""

    if (
        state.get("pending_interrupt")
        and state.get("focus_worker_id")
    ):
        pending = state["pending_interrupt"]

        context_hint = (
            "Агент только что задал пользователю вопрос: "
            f"{pending['question']!r}. "
            "Если последняя реплика является ответом на этот вопрос, "
            "выбери intent='resume_previous'."
        )

    recent_messages = list(state["messages"][-10:])

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "supervisor",
            fallback=_SUPERVISOR_FALLBACK_PROMPT,
        ),
        extra_context=(
            {"hint": context_hint}
            if context_hint
            else None
        ),
        output_contract="JSON строго по схеме IntentClassification.",
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            *recent_messages,
        ],
        IntentClassification,
        worker_kind="supervisor",
    )

    return {
        "intent": decision.intent,
        "intent_confidence": decision.confidence,
        "_incident_number": decision.incident_number,
        "_raw_description": decision.raw_description,
        "_resolved_query": decision.resolved_query,
        "_evidence": decision.evidence,
        "turn_count": state["turn_count"] + 1,
    }


async def _spawn_and_run(
    *,
    kind: str,
    input_context: dict[str, Any],
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> tuple[dict[str, Any] | None, SpawnError | None]:
    spawned = spawn_worker(
        kind=kind,
        input_context=input_context,
        state=state,
    )

    if isinstance(spawned, SpawnError):
        return None, spawned

    graph = subgraphs.get(kind)

    if graph is None:
        return None, SpawnError(
            reason=f"Compiled subgraph for {kind!r} is not available",
        )

    worker = await graph.ainvoke(
        spawned.arg,
        config=config,
    )

    return worker, None


def _error_command(
    error: SpawnError,
) -> Command:
    return Command(
        update={
            "messages": [
                AIMessage(content=error.to_user_message())
            ]
        }
    )


def _current_report(
    state: OrchestratorState,
) -> tuple[str, dict[str, Any]] | None:
    artifact_id = state.get("current_artifact_id")

    if not artifact_id:
        return None

    artifact = state["artifacts"].get(artifact_id)

    if artifact is None:
        return None

    if artifact["kind"] != "incident_report":
        return None

    return artifact_id, artifact


def _presentation_source_context(
    state: OrchestratorState,
) -> dict[str, Any]:
    """
    Формирует creator input.

    Приоритет:
    1. текущий incident_report;
    2. incident_number из planner;
    3. raw_description из planner;
    4. последняя user message.
    """
    report = _current_report(state)

    if report is not None:
        artifact_id, artifact = report
        sections = artifact["versions"][
            artifact["current_version"]
        ]["sections"]

        rca_input = sections.get("_rca_input") or {}

        return {
            "payload": {
                "source_artifact_id": artifact_id,
            },
            "artifact_id": artifact_id,
            "artifact_sections": sections,
            "incident_number": rca_input.get(
                "incident_number"
            ),
        }

    incident_number = state.get("_incident_number")

    if incident_number:
        return {
            "payload": {},
            "incident_number": incident_number,
        }

    source_text = (
        state.get("_raw_description")
        or state["messages"][-1].content
    )

    return {
        "payload": {},
        "source_text": str(source_text),
    }


async def _save_presentation(
    *,
    state: OrchestratorState,
    worker: dict[str, Any],
) -> dict[str, Any]:
    """
    Сохраняет completed creator document в memory.

    Creator не пишет в DB. Ownership берётся из root state.user_id.
    """
    summary = worker.get("summary_for_parent") or {}
    raw_document = summary.get("document")

    if not isinstance(raw_document, dict):
        raise ValueError(
            "Creator worker completed without PresentationDocument"
        )

    document = PresentationDocument.model_validate(
        raw_document,
    )

    presentation_id = await get_memory().create_presentation(
        user_id=state["user_id"],
        thread_id=state["thread_id"],
        fields=document,
    )

    artifact = {
        "id": presentation_id,
        "kind": "presentation_reference",
        "status": "draft",
        "versions": [
            {
                "version": 0,
                "sections": {
                    "presentation_id": presentation_id,
                    "document": document.model_dump(mode="json"),
                },
                "produced_by_worker_id": worker["worker_id"],
                "note": "Presentation stored in memory",
                "timestamp": worker.get(
                    "created_at",
                    "",
                ),
            }
        ],
        "current_version": 0,
        "locked_for_editing": False,
        "created_by_worker_id": worker["worker_id"],
        "created_at": worker.get("created_at", ""),
    }

    download_url = (
        f"/api/v1/presentations/{presentation_id}/file"
        "?version=draft"
    )

    return {
        "artifacts": {
            **state["artifacts"],
            presentation_id: artifact,
        },
        "current_artifact_id": presentation_id,
        "messages": [
            AIMessage(
                content=(
                    "Презентация готова и сохранена в «Моих презентациях». "
                    f"[Скачать HTML-файл]({download_url})."
                )
            )
        ],
    }


@register_intent("new_search")
async def on_new_search(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    raw_query = (
        state.get("_resolved_query")
        or state["messages"][-1].content
    )

    worker, error = await _spawn_and_run(
        kind="search",
        input_context={
            "payload": {
                "raw_query": str(raw_query),
            }
        },
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if error is not None:
        return _error_command(error)

    return Command(
        update=finalize_worker(state, worker)
    )


@register_intent("search_then_analyze")
async def on_search_then_analyze(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    raw_query = (
        state.get("_resolved_query")
        or state["messages"][-1].content
    )

    search_worker, search_error = await _spawn_and_run(
        kind="search",
        input_context={
            "payload": {
                "raw_query": str(raw_query),
            }
        },
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if search_error is not None:
        return _error_command(search_error)

    search_update = finalize_worker(
        state,
        search_worker,
    )

    if search_worker["status"] != "done":
        return Command(update=search_update)

    rca_state = {
        **state,
        **search_update,
    }

    rca_worker, rca_error = await _spawn_and_run(
        kind="rca",
        input_context={
            "payload": {},
            "parent_worker_id": search_worker["worker_id"],
            "search_summary": search_worker[
                "summary_for_parent"
            ],
        },
        state=rca_state,
        config=config,
        subgraphs=subgraphs,
    )

    if rca_error is not None:
        search_update["messages"] = [
            *search_update.get("messages", []),
            AIMessage(content=rca_error.to_user_message()),
        ]
        return Command(update=search_update)

    rca_update = finalize_worker(
        rca_state,
        rca_worker,
    )

    report_update = create_rca_report_update(
        rca_state,
        rca_worker,
    )

    merged_messages = [
        *search_update.get("messages", []),
        *rca_update.get("messages", []),
    ]

    return Command(
        update={
            **search_update,
            **rca_update,
            **report_update,
            "workers": {
                **search_update["workers"],
                **rca_update["workers"],
            },
            "messages": merged_messages,
        }
    )


@register_intent("analyze")
async def on_analyze(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    incident_number = state.get("_incident_number")
    raw_description = state.get("_raw_description")

    if not incident_number and not raw_description:
        return Command(
            update={
                "messages": [
                    AIMessage(
                        content=(
                            "Укажите номер инцидента или опишите проблему, "
                            "которую нужно разобрать."
                        )
                    )
                ]
            }
        )

    input_context: dict[str, Any] = {
        "payload": {},
    }

    if incident_number:
        input_context["incident_number"] = incident_number
    else:
        input_context["raw_description"] = raw_description

    worker, error = await _spawn_and_run(
        kind="rca",
        input_context=input_context,
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if error is not None:
        return _error_command(error)

    update = finalize_worker(state, worker)
    update.update(
        create_rca_report_update(state, worker)
    )

    return Command(update=update)


@register_intent("reanalyze_report")
async def on_reanalyze_report(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    report = _current_report(state)

    if report is None:
        return Command(
            update={
                "messages": [
                    AIMessage(
                        content=(
                            "Сначала нужен готовый RCA-отчёт, чтобы "
                            "пересчитать его с новыми фактами."
                        )
                    )
                ]
            }
        )

    artifact_id, artifact = report

    evidence = (
        state.get("_evidence")
        or state["messages"][-1].content
    )
    evidence = str(evidence).strip()

    if not evidence:
        return Command(
            update={
                "messages": [
                    AIMessage(
                        content=(
                            "Не удалось выделить новый факт для повторного "
                            "RCA. Опишите лог, метрику или результат "
                            "проверки подробнее."
                        )
                    )
                ]
            }
        )

    sections = artifact["versions"][
        artifact["current_version"]
    ]["sections"]

    old_rca_input = sections.get("_rca_input") or {}

    worker, error = await _spawn_and_run(
        kind="rca",
        input_context={
            "payload": {},
            "incident_number": old_rca_input.get(
                "incident_number"
            ),
            "raw_description": old_rca_input.get(
                "raw_description"
            ),
            "search_summary": old_rca_input.get(
                "search_summary"
            ),
            "evidence": [
                *list(old_rca_input.get("evidence") or []),
                evidence,
            ],
        },
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if error is not None:
        return _error_command(error)

    update = finalize_worker(state, worker)

    update.update(
        replace_rca_report_update(
            state,
            worker,
            artifact_id=artifact_id,
            evidence_text=evidence,
        )
    )

    return Command(update=update)


@register_intent("edit_report")
async def on_edit_report(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    report = _current_report(state)

    if report is None:
        return Command(
            update={
                "messages": [
                    AIMessage(
                        content=(
                            "Пока нечего редактировать: сначала нужен "
                            "готовый RCA-отчёт."
                        )
                    )
                ]
            }
        )

    artifact_id, artifact = report
    sections = artifact["versions"][
        artifact["current_version"]
    ]["sections"]

    system = llm_client.build_system_message(
        role_instruction=get_prompt(
            "editor_intent",
            fallback=_EDIT_FALLBACK_PROMPT,
        ),
        extra_context={
            "tasks": [
                {
                    "number": index + 1,
                    "title": task.get("title"),
                }
                for index, task in enumerate(
                    sections.get("tasks") or []
                )
                if isinstance(task, dict)
            ]
        },
        output_contract="JSON строго по схеме EditRequest.",
    )

    request = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(
                content=state["messages"][-1].content
            ),
        ],
        EditRequest,
        worker_kind="editor",
    )

    task_index = (
        request.task_number - 1
        if request.task_number is not None
        else None
    )

    input_context: dict[str, Any] = {
        "payload": {
            "target_artifact_id": artifact_id,
            "target_section": request.target_section,
            "instruction": request.instruction,
            "task_index": task_index,
        },
        "artifact_id": artifact_id,
        "current_section_value": sections.get(
            request.target_section
        ),
    }

    if request.target_section == "tasks":
        input_context["current_tasks"] = sections.get(
            "tasks"
        ) or []
        input_context["rca_input"] = sections.get(
            "_rca_input"
        ) or {}

    worker, error = await _spawn_and_run(
        kind="editor",
        input_context=input_context,
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if error is not None:
        return _error_command(error)

    return Command(
        update=finalize_editor_result(
            state,
            worker,
        )
    )


@register_intent("create_presentation")
async def on_create_presentation(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    worker, error = await _spawn_and_run(
        kind="creator",
        input_context=_presentation_source_context(state),
        state=state,
        config=config,
        subgraphs=subgraphs,
    )

    if error is not None:
        return _error_command(error)

    update = finalize_worker(state, worker)

    if worker["status"] != "done":
        return Command(update=update)

    try:
        save_update = await _save_presentation(
            state=state,
            worker=worker,
        )
    except Exception:
        return Command(
            update={
                **update,
                "messages": [
                    *update.get("messages", []),
                    AIMessage(
                        content=(
                            "Документ презентации собран, но сохранить "
                            "его в хранилище не удалось."
                        )
                    ),
                ],
            }
        )

    return Command(
        update={
            **update,
            **save_update,
            "messages": [
                *update.get("messages", []),
                *save_update["messages"],
            ],
        }
    )


@register_intent("resume_previous")
async def on_resume_previous(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    """
    Resume native LangGraph interrupt выполняется API через Command(resume=...),
    поэтому здесь не нужно повторно запускать focused worker.

    Этот intent нужен только для возврата к worker, который был отложен
    пользователем при переключении на другую задачу.
    """
    restored = resume_focus(state)

    if restored is None:
        return Command(
            update={
                "messages": [
                    AIMessage(
                        content="Не нашёл задачу, к которой можно вернуться."
                    )
                ]
            }
        )

    return Command(update=restored)


@register_intent("cancel_current")
async def on_cancel_current(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    return Command(
        update={
            "focus_worker_id": None,
            "pending_interrupt": None,
            "messages": [
                AIMessage(
                    content="Хорошо, текущую задачу отменил."
                )
            ],
        }
    )


@register_intent("chitchat_or_other")
async def on_chitchat_or_other(
    state: OrchestratorState,
    config: dict[str, Any],
    subgraphs: dict[str, Any],
) -> Command:
    response = await llm_client.ainvoke(
        [
            llm_client.build_system_message(
                role_instruction=get_prompt(
                    "chitchat",
                    fallback=_CHITCHAT_FALLBACK_PROMPT,
                )
            ),
            state["messages"][-1],
        ],
        worker_kind="supervisor",
    )

    return Command(
        update={
            "messages": [
                AIMessage(content=str(response.content))
            ]
        }
    )


async def run_worker(
    state: OrchestratorState,
    config: dict[str, Any],
) -> Command:
    subgraphs = _get_subgraphs()

    handler = INTENT_REGISTRY.get(
        state["intent"],
        on_chitchat_or_other,
    )

    return await handler(
        state,
        config,
        subgraphs,
    )


def build_orchestrator_graph(
    *,
    checkpointer: Any,
):
    """
    Root conversation graph.

    MemoryFacade настраивается отдельно через
    runtime.services.configure_runtime_services(memory=...).
    """
    graph = StateGraph(OrchestratorState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("run_worker", run_worker)

    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "run_worker")
    graph.add_edge("run_worker", END)

    return graph.compile(checkpointer=checkpointer)