from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

from app.ai.runtime.agent_history import append_agent_event
from app.ai.runtime.interaction_factory import build_interaction
from app.ai.runtime.task_lifecycle import (
    advance_stage,
    complete_active,
    set_awaiting_input,
)
from app.ai.schemas.conversation import (
    ConversationPlan,
    ConversationTask,
    SearchResultRef,
    SelectOption,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.registry import register_workflow
from app.ai.workflows.search.contracts import (
    CatalogCandidate,
    SearchNormalizationDecision,
)
from app.ai.workflows.search.memory_adapter import execute_search
from app.ai.workflows.search.normalizer_agent import (
    NormalizerOutcome,
    SearchNormalizerError,
    run_normalizer,
)
from app.ai.workflows.updates import (
    merge_state_updates,
    status_message,
    user_message,
)
from app.memory.search.markdown import (
    render_search_preview_markdown,
)
from app.ai.schemas.conversation import (
    LastSearchContext,
    utc_now_iso,
)
from app.ai.workflows.search.presentation import (
    catalog_candidate_label,
    catalog_candidates_for_ids,
    render_interaction_text,
)

_SEARCH_AGENT = "search_normalizer"


@register_workflow("search")
def create_search_workflow() -> "SearchWorkflow":
    return SearchWorkflow()


class SearchWorkflow:
    """
    Search workflow для самостоятельного поиска.

    Для RCA/Presentation dependency-search будет использовать тот же
    normalizer/executor, но не этот workflow напрямую: родительский workflow
    сохраняет task.kind="rca"/"presentation" и вызывает shared search stages.
    """

    async def start(
        self,
        state: ConversationState,
        task: ConversationTask,
    ) -> dict:
        plan = _load_conversation_plan(task)

        if plan.intent != "search":
            return user_message(
                "Не удалось подготовить параметры поиска."
            )

        query = plan.search_query or task.goal

        return await self._normalize(
            state=state,
            task=task,
            user_text=query,
            status_text="Уточняю параметры поиска.",
        )

    async def resume(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        """
        Возобновляет normalizer после select_candidate или clarify.

        Текст не парсится Python-кодом: normalizer получает сохранённую
        локальную history (включая реальных candidates/question) и решает,
        что пользователь выбрал или уточнил.
        """
        stage = task.snapshot.stage

        if stage not in {
            "search.await_candidate_selection",
            "search.await_clarification",
        }:
            return user_message(
                "Не понял, к какому этапу поиска относится этот ответ."
            )

        return await self._normalize(
            state=state,
            task=task,
            user_text=user_text,
            status_text="Обновляю параметры поиска.",
        )

    async def continue_task(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        """
        Сообщение продолжает текущий поиск без pending interaction.

        Пока normalizer обрабатывает его как уточнение исходной задачи:
        история сохраняет предыдущее решение и search result ref.
        """
        return await self._normalize(
            state=state,
            task=task,
            user_text=user_text,
            status_text="Уточняю параметры поиска.",
        )

    async def refine(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
        goal_hint: str | None,
    ) -> dict:
        """
        Изменение фильтров или условий прежнего поиска.

        `goal_hint` сохраняем только как LLM Guard hint: он не заменяет
        оригинальный текст пользователя и не превращается в filter Python-ом.
        """
        data = append_agent_event(
            task.snapshot.data,
            agent=_SEARCH_AGENT,
            role="system",
            kind="guard_refinement",
            payload={
                "goal_hint": goal_hint or "",
            },
        )

        refined_task = task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"data": data}
                )
            }
        )

        return await self._normalize(
            state=state,
            task=refined_task,
            user_text=user_text,
            status_text="Уточняю параметры поиска.",
        )

    async def _normalize(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
        status_text: str,
    ) -> dict:
        try:
            outcome = await run_normalizer(
                snapshot=task.snapshot,
                user_text=user_text,
            )
        except SearchNormalizerError:
            return user_message(
                "Не удалось нормализовать параметры поиска. "
                "Попробуйте сформулировать запрос иначе."
            )

        return await self._apply_normalization(
            state=state,
            task=task,
            outcome=outcome,
            status_text=status_text,
        )

    async def _apply_normalization(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        outcome: NormalizerOutcome,
        status_text: str,
    ) -> dict:
        decision = outcome.decision

        if decision.action == "select_candidate":
            return _await_candidate_selection(
                state=state,
                task=task,
                outcome=outcome,
                status_text=status_text,
            )

        if decision.action == "clarify":
            return _await_clarification(
                state=state,
                task=task,
                outcome=outcome,
                status_text=status_text,
            )

        if decision.action == "execute":
            return await _execute(
                state=state,
                task=task,
                outcome=outcome,
                status_text=status_text,
            )

        return user_message(
            "Не удалось определить следующий шаг поиска."
        )


def _load_conversation_plan(
    task: ConversationTask,
) -> ConversationPlan:
    raw_plan = task.snapshot.data.get("plan")

    if not isinstance(raw_plan, dict):
        raise ValueError(
            "Search task has no serialized ConversationPlan"
        )

    return ConversationPlan.model_validate(raw_plan)


def _await_candidate_selection(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    status_text: str,
) -> dict:
    decision = outcome.decision
    candidates = catalog_candidates_for_ids(
        snapshot_data=outcome.snapshot_data,
        candidate_ids=decision.option_ids,
    )

    if len(candidates) != len(decision.option_ids):
        return user_message(
            "Не удалось подготовить варианты выбора для поиска."
        )

    options = [
        SelectOption(
            value=candidate.id,
            label=catalog_candidate_label(candidate),
            description=f"Совпадение: {candidate.score:.0f}%",
        )
        for candidate in candidates
    ]

    interaction = build_interaction(
        owner="search",
        continuation_stage="search.await_candidate_selection",
        kind="single_select",
        question=decision.question or "Выберите нужный вариант.",
        options=options,
        metadata={
            "purpose": "search_candidate_selection",
        },
    )

    lifecycle_update = set_awaiting_input(
        state,
        interaction=interaction,
        stage=interaction.continuation_stage,
        data={
            "agent_history": (
                outcome.snapshot_data.get("agent_history") or {}
            ),
            "last_normalization_decision": (
                decision.model_dump(mode="json")
            ),
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message(status_text),
        user_message(render_interaction_text(interaction)),
    )


def _await_clarification(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    status_text: str,
) -> dict:
    decision = outcome.decision

    interaction = build_interaction(
        owner="search",
        continuation_stage="search.await_clarification",
        kind="free_text",
        question=decision.question or (
            "Уточните, пожалуйста, параметры поиска."
        ),
        metadata={
            "purpose": "search_clarification",
        },
    )

    lifecycle_update = set_awaiting_input(
        state,
        interaction=interaction,
        stage=interaction.continuation_stage,
        data={
            "agent_history": (
                outcome.snapshot_data.get("agent_history") or {}
            ),
            "last_normalization_decision": (
                decision.model_dump(mode="json")
            ),
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message(status_text),
        user_message(render_interaction_text(interaction)),
    )


async def _execute(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    status_text: str,
) -> dict:
    decision = outcome.decision
    search_plan = decision.plan

    if search_plan is None:
        return user_message(
            "Не удалось подготовить план поиска."
        )

    try:
        result = await execute_search(
            plan=search_plan,
            user_id=state["user_id"],
            thread_id=state["thread_id"],
        )
    except Exception:
        return user_message(
            "Поиск временно недоступен. Попробуйте ещё раз позже."
        )

    result_ref = SearchResultRef(
        id=result.result_id,
        label=(
            f"Поиск: найдено {result.total_count} результатов"
        ),
    )

    data = append_agent_event(
        outcome.snapshot_data,
        agent="search_executor",
        role="tool",
        kind="execute_search",
        payload={
            "plan": search_plan.model_dump(mode="json"),
            "result_id": result.result_id,
            "entity": result.entity,
            "total_count": result.total_count,
            "preview_count": result.preview_count,
        },
    )

    lifecycle_update = advance_stage(
        state,
        stage="search.completed",
        data={
            "agent_history": data.get("agent_history") or {},
            "search_result": {
                "result_id": result.result_id,
                "entity": result.entity,
                "total_count": result.total_count,
                "preview_count": result.preview_count,
            },
            "search_plan": search_plan.model_dump(mode="json"),
        },
        refs=[result_ref],
    )

    rendered = render_search_preview_markdown(
        result.artifact
    )

    if result.preview_count < result.total_count:
        rendered += (
            "\n\n"
            f"[Показать все {result.total_count} результатов]"
            f"(/api/v1/search-results/{result.result_id})"
        )

    completion_update = complete_active(
        {
            **state,
            **lifecycle_update,
        }

    )

    last_search = LastSearchContext(
        result_ref=result_ref,
        plan=search_plan.model_dump(mode="json"),
        goal=task.goal,
        completed_at=utc_now_iso(),
    )


    return merge_state_updates(
        lifecycle_update,
        status_message("Ищу инциденты или поручения."),
        completion_update,
        {
            "last_search": last_search,
        },
        user_message(rendered),
    )
