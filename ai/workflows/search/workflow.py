from __future__ import annotations

import logging
from typing import Any

from app.ai.runtime.task_lifecycle import get_active_task, get_step_run
from app.ai.runtime.services import get_memory
from app.ai.schemas.conversation import (
    IncidentRef,
    PlanStep,
    SearchResultRef,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.dispatcher import build_worker_result, ensure_step_kind
from app.ai.workflows.executor import StepExecutionResult
from app.ai.workflows.search.contracts import SearchPlan
from app.ai.workflows.search.resolver import (
    SearchResolverError,
    resolve_search_request,
)
from app.ai.workflows.search.search_agent import (
    SearchAgentError,
    plan_search,
)
from app.memory.search.contracts import SearchResultReferenceArtifact
from app.memory.search.markdown import render_search_preview_markdown


logger = logging.getLogger(__name__)


async def execute_search_step(
    state: ConversationState,
    step: PlanStep,
) -> StepExecutionResult:
    """
    Исполняет один search-шаг ExecutionPlan.

    Executor до вызова worker-а добавляет `step.goal` первым user-сообщением
    истории шага. Worker не меняет ConversationTask: он только возвращает
    изолированный StepExecutionResult.
    """
    ensure_step_kind(step, "search")

    task = get_active_task(state)
    step_run = get_step_run(task, step_id=step.step_id)
    conversation = step_run.conversation

    try:
        normalized_request = await resolve_search_request(
            conversation=conversation,
        )
        search_plan = await plan_search(
            conversation=conversation,
            normalized_request=normalized_request,
        )
    except SearchResolverError:
        logger.warning(
            "Search resolver failed: task_id=%s step_id=%s",
            task.task_id,
            step.step_id,
            exc_info=True,
        )
        return _clarification_result(
            step=step,
            question=(
                "Не удалось уточнить параметры поиска. "
                "Напишите, что нужно найти или посчитать."
            ),
        )
    except SearchAgentError:
        logger.warning(
            "Search Agent failed: task_id=%s step_id=%s",
            task.task_id,
            step.step_id,
            exc_info=True,
        )
        return _clarification_result(
            step=step,
            question=(
                "Не удалось подготовить поиск. "
                "Уточните объект, период или показатель."
            ),
        )

    if search_plan.mode == "clarify":
        return _clarification_result(
            step=step,
            question=search_plan.question or "Уточните параметры поиска.",
        )

    if search_plan.mode == "records":
        return await _execute_records(
            state=state,
            search_plan=search_plan,
        )

    if search_plan.mode == "analytics":
        return await _execute_analytics(search_plan=search_plan)

    raise RuntimeError(f"Unsupported search mode: {search_plan.mode!r}.")


async def _execute_records(
    *,
    state: ConversationState,
    search_plan: SearchPlan,
) -> StepExecutionResult:
    records = search_plan.records
    if records is None:
        raise RuntimeError("Records SearchPlan has no records payload.")

    artifact = await get_memory().search(
        entity=records.entity,
        user_id=state["user_id"],
        thread_id=state["thread_id"],
        filters=records.filters,
        semantic_query=(
            records.semantic.query
            if records.semantic is not None
            else None
        ),
        sorts=[sort.model_dump(mode="json") for sort in records.sorts],
        top_n=records.top_n,
        preview_limit=10,
    )

    search_ref = SearchResultRef(
        id=artifact.result_id,
        label=_search_result_label(artifact),
    )
    output_refs = [search_ref]

    incident_ref = _single_incident_ref(artifact)
    if incident_ref is not None:
        output_refs.append(incident_ref)

    return build_worker_result(
        output_refs=output_refs,
        assistant_message=_render_records_result(artifact),
        state_update={"last_status": "Поиск завершён."},
    )


async def _execute_analytics(
    *,
    search_plan: SearchPlan,
) -> StepExecutionResult:
    analytics = search_plan.analytics
    if analytics is None:
        raise RuntimeError("Analytics SearchPlan has no analytics payload.")

    result = await get_memory().query_analytics_sql(
        sql=analytics.sql,
        parameters=analytics.parameters,
        max_rows=analytics.max_rows,
    )

    return build_worker_result(
        output_refs=[],
        assistant_message=_render_analytics(
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
        ),
        state_update={"last_status": "Аналитический запрос выполнен."},
    )


def _clarification_result(
    *,
    step: PlanStep,
    question: str,
) -> StepExecutionResult:
    return build_worker_result(
        output_refs=[],
        assistant_message=question,
        user_input_request={
            "request_id": f"{step.step_id}-input",
            "kind": "ask_user",
            "question": question,
        },
        state_update={"last_status": "Нужно уточнение для поиска."},
    )


def _single_incident_ref(
    artifact: SearchResultReferenceArtifact,
) -> IncidentRef | None:
    """Строит IncidentRef только для строго единственного результата поиска."""
    if artifact.entity != "incidents":
        return None

    if artifact.total_count != 1 or len(artifact.preview.rows) != 1:
        return None

    row = artifact.preview.rows[0]
    number = str(row.values.get("number") or row.entity_id).strip()
    if not number:
        return None

    label = str(
        row.values.get("description")
        or row.values.get("short_description")
        or row.values.get("system_name")
        or number
    ).strip()

    return IncidentRef(
        id=row.entity_id,
        number=number,
        label=label or number,
    )


def _search_result_label(
    artifact: SearchResultReferenceArtifact,
) -> str:
    entity_label = (
        "инциденты"
        if artifact.entity == "incidents"
        else "поручения"
    )
    return f"Результат поиска: {entity_label} ({artifact.total_count})"


def _render_records_result(
    artifact: SearchResultReferenceArtifact,
) -> str:
    try:
        return render_search_preview_markdown(artifact)
    except Exception:
        logger.exception(
            "Could not render search preview: result_id=%s",
            artifact.result_id,
        )
        return f"Поиск завершён. Найдено результатов: {artifact.total_count}."


def _render_analytics(
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
) -> str:
    if not rows:
        return "По указанным критериям данных не найдено."

    visible_columns = columns[:10]
    lines = [
        "| " + " | ".join(visible_columns) + " |",
        "| " + " | ".join("---" for _ in visible_columns) + " |",
    ]

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _escape_table_value(row.get(column))
                for column in visible_columns
            )
            + " |"
        )

    if truncated:
        lines.extend(["", "Показана только первая часть результата."])

    return "\n".join(lines)


def _escape_table_value(value: Any) -> str:
    if value is None:
        return "—"

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )