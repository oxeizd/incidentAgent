from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.ai.runtime.agent_history import append_agent_event
from app.ai.runtime.interaction_factory import build_interaction
from app.ai.schemas.conversation import (
    ConversationTask,
    IncidentRef,
    SearchResultRef,
    SelectOption,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.search.contracts import (
    SearchIncidentCandidate,
    SearchNormalizationDecision,
)
from app.ai.workflows.search.incident_selection_agent import (
    IncidentSelectionError,
    decide_incident_selection,
    interpret_incident_answer,
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
from app.ai.workflows.search.presentation import (
    catalog_candidate_label,
    catalog_candidates_for_ids,
    incident_candidates_from_preview,
    render_interaction_text,
)

ParentKind = Literal["rca", "presentation"]

_DEPENDENCY_AGENT = "search_dependency"


@dataclass(frozen=True, slots=True)
class DependencySearchResult:
    """
    Result dependency Search stage.

    `update` — state update после выполнения текущего шага.
    `incident_ref` — появляется только когда Search завершён и пользователь
    выбрал один incident. Parent workflow проверяет `is_complete`, затем
    переходит к RCA/presentation stage.
    """

    update: dict[str, Any]
    incident_ref: IncidentRef | None = None

    @property
    def is_complete(self) -> bool:
        return self.incident_ref is not None


class DependencySearchError(RuntimeError):
    """Контролируемая ошибка internal Search dependency stage."""


async def start_dependency_search(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    query: str,
) -> DependencySearchResult:
    """
    Начинает Search stage внутри уже созданной RCA/Presentation task.

    task.kind остаётся `rca` или `presentation`: пользователь не видит
    внутреннего переключения workflow, а active task lifecycle не меняется.
    """
    return await _run_normalization(
        state=state,
        task=task,
        parent_kind=parent_kind,
        user_text=query,
    )


async def resume_dependency_search(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    user_text: str,
) -> DependencySearchResult:
    """
    Продолжает dependency search на нужном internal stage.

    Search normalizer и incident selector используют локальную историю
    родительской task, но под разными agent names.
    """
    stage = task.snapshot.stage

    if stage in {
        "search.dependency.await_candidate_selection",
        "search.dependency.await_clarification",
    }:
        return await _run_normalization(
            state=state,
            task=task,
            parent_kind=parent_kind,
            user_text=user_text,
        )

    if stage == "search.dependency.await_incident_selection":
        return await _resume_incident_selection(
            state=state,
            task=task,
            parent_kind=parent_kind,
            user_text=user_text,
        )

    raise DependencySearchError(
        f"Cannot resume dependency search from stage {stage!r}"
    )


async def refine_dependency_search(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    user_text: str,
    goal_hint: str | None,
) -> DependencySearchResult:
    """
    Изменяет параметры уже запущенного Search dependency.

    Например: «только за май», «не по Stalker Core, а Reporting».
    """
    data = append_agent_event(
        task.snapshot.data,
        agent=_DEPENDENCY_AGENT,
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

    return await _run_normalization(
        state=state,
        task=refined_task,
        parent_kind=parent_kind,
        user_text=user_text,
    )


async def _run_normalization(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    user_text: str,
) -> DependencySearchResult:
    try:
        outcome = await run_normalizer(
            snapshot=task.snapshot,
            user_text=user_text,
        )
    except SearchNormalizerError:
        return DependencySearchResult(
            update=user_message(
                "Не удалось уточнить параметры поиска. "
                "Сформулируйте запрос немного иначе."
            )
        )

    return await _apply_normalization(
        state=state,
        task=task,
        parent_kind=parent_kind,
        outcome=outcome,
    )


async def _apply_normalization(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    outcome: NormalizerOutcome,
) -> DependencySearchResult:
    decision = outcome.decision

    if decision.action == "select_candidate":
        return DependencySearchResult(
            update=_await_catalog_selection(
                state=state,
                task=task,
                outcome=outcome,
            )
        )

    if decision.action == "clarify":
        return DependencySearchResult(
            update=_await_search_clarification(
                state=state,
                task=task,
                outcome=outcome,
            )
        )

    if decision.action == "execute":
        return await _execute_and_select_incident(
            state=state,
            task=task,
            parent_kind=parent_kind,
            outcome=outcome,
        )

    return DependencySearchResult(
        update=user_message(
            "Не удалось определить следующий шаг поиска."
        )
    )


def _await_catalog_selection(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
) -> dict:
    """
    Та же Interaction для выбора каталожной сущности, что и в standalone
    SearchWorkflow, но task.kind остаётся родительским.
    """
    from app.ai.runtime.task_lifecycle import set_awaiting_input

    decision = outcome.decision
    candidates = catalog_candidates_for_ids(
        snapshot_data=outcome.snapshot_data,
        candidate_ids=decision.option_ids,
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
        owner=task.kind,
        continuation_stage=(
            "search.dependency.await_candidate_selection"
        ),
        kind="single_select",
        question=decision.question or "Выберите нужный вариант.",
        options=options,
        metadata={
            "purpose": "dependency_search_catalog_selection",
            "parent_kind": task.kind,
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
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Уточняю параметры поиска."),
        user_message(render_interaction_text(interaction)),
    )


def _await_search_clarification(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
) -> dict:
    from app.ai.runtime.task_lifecycle import set_awaiting_input

    interaction = build_interaction(
        owner=task.kind,
        continuation_stage="search.dependency.await_clarification",
        kind="free_text",
        question=outcome.decision.question or (
            "Уточните параметры поиска."
        ),
        metadata={
            "purpose": "dependency_search_clarification",
            "parent_kind": task.kind,
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
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Уточняю параметры поиска."),
        user_message(render_interaction_text(interaction)),
    )


async def _execute_and_select_incident(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    outcome: NormalizerOutcome,
) -> DependencySearchResult:
    search_plan = outcome.decision.plan

    if search_plan is None:
        return DependencySearchResult(
            update=user_message(
                "Не удалось подготовить план поиска."
            )
        )

    try:
        result = await execute_search(
            plan=search_plan,
            user_id=state["user_id"],
            thread_id=state["thread_id"],
        )
    except Exception:
        return DependencySearchResult(
            update=user_message(
                "Поиск временно недоступен. Попробуйте позже."
            )
        )

    if result.entity != "incidents":
        return DependencySearchResult(
            update=user_message(
                "Для продолжения нужен поиск инцидентов, "
                "а не поручений. Уточните запрос."
            )
        )

    result_ref = SearchResultRef(
        id=result.result_id,
        label=(
            f"Поиск инцидентов: "
            f"найдено {result.total_count}"
        ),
    )

    candidates = incident_candidates_from_preview(
        result.artifact.preview.rows
    )

    if not candidates:
        return await _ask_to_refine_empty_preview(
            state=state,
            task=task,
            outcome=outcome,
            result_ref=result_ref,
        )

    try:
        selection = await decide_incident_selection(
            candidates=candidates,
            parent_kind=parent_kind,
        )
    except IncidentSelectionError:
        return DependencySearchResult(
            update=user_message(
                "Не удалось подготовить выбор инцидента. "
                "Уточните условия поиска."
            )
        )

    if selection.action == "refine_search":
        return _await_refine_after_results(
            state=state,
            task=task,
            outcome=outcome,
            result_ref=result_ref,
            question=selection.question,
        )

    return _await_incident_selection(
        state=state,
        task=task,
        outcome=outcome,
        result_ref=result_ref,
        search_plan=search_plan.model_dump(mode="json"),
        candidates=candidates,
        question=selection.question,
        option_entity_ids=selection.option_entity_ids,
    )


async def _ask_to_refine_empty_preview(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    result_ref: SearchResultRef,
) -> DependencySearchResult:
    """
    Search сохранил result, но preview не содержит пригодных для RCA rows.

    Не передаём управление RCA и не строим IncidentRef на догадках.
    """
    return _await_refine_after_results(
        state=state,
        task=task,
        outcome=outcome,
        result_ref=result_ref,
        question=(
            "Поиск не вернул подходящих инцидентов для выбора. "
            "Уточните систему, период, симптомы или номер инцидента."
        ),
    )


def _await_refine_after_results(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    result_ref: SearchResultRef,
    question: str,
) -> DependencySearchResult:
    from app.ai.runtime.task_lifecycle import set_awaiting_input

    interaction = build_interaction(
        owner=task.kind,
        continuation_stage="search.dependency.await_clarification",
        kind="free_text",
        question=question,
        metadata={
            "purpose": "dependency_search_refinement",
            "parent_kind": task.kind,
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
            "search_result_ref": result_ref.model_dump(
                mode="json"
            ),
        },
    )

    return DependencySearchResult(
        update=merge_state_updates(
            lifecycle_update,
            status_message("Ищу подходящий инцидент."),
            user_message(render_interaction_text(interaction)),
        )
    )


def _await_incident_selection(
    *,
    state: ConversationState,
    task: ConversationTask,
    outcome: NormalizerOutcome,
    result_ref: SearchResultRef,
    search_plan: dict[str, Any],
    candidates: list[SearchIncidentCandidate],
    question: str,
    option_entity_ids: list[str],
) -> DependencySearchResult:
    from app.ai.runtime.task_lifecycle import set_awaiting_input

    candidates_by_id = {
        candidate.entity_id: candidate
        for candidate in candidates
    }

    options = [
        SelectOption(
            value=candidate_id,
            label=candidates_by_id[candidate_id].label,
            ref=IncidentRef(
                id=candidates_by_id[candidate_id].entity_id,
                number=candidates_by_id[candidate_id].number,
                label=candidates_by_id[candidate_id].label,
            ),
        )
        for candidate_id in option_entity_ids
        if candidate_id in candidates_by_id
    ]

    if not options:
        return DependencySearchResult(
            update=user_message(
                "Не удалось подготовить варианты инцидентов."
            )
        )

    interaction = build_interaction(
        owner=task.kind,
        continuation_stage=(
            "search.dependency.await_incident_selection"
        ),
        kind="single_select",
        question=question,
        options=options,
        metadata={
            "purpose": "dependency_search_incident_selection",
            "parent_kind": task.kind,
        },
    )

    history_data = append_agent_event(
        outcome.snapshot_data,
        agent=_DEPENDENCY_AGENT,
        role="assistant",
        kind="incident_selection_question",
        payload={
            "question": question,
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in candidates
                if candidate.entity_id in set(option_entity_ids)
            ],
        },
    )

    lifecycle_update = set_awaiting_input(
        state,
        interaction=interaction,
        stage=interaction.continuation_stage,
        data={
            "agent_history": (
                history_data.get("agent_history") or {}
            ),
            "search_result_ref": result_ref.model_dump(
                mode="json"
            ),
            "search_plan": search_plan,
            "incident_selection_candidates": [
                candidate.model_dump(mode="json")
                for candidate in candidates
                if candidate.entity_id in set(option_entity_ids)
            ],
        },
    )

    return DependencySearchResult(
        update=merge_state_updates(
            lifecycle_update,
            status_message("Нашёл возможные инциденты."),
            user_message(render_interaction_text(interaction)),
        )
    )


async def _resume_incident_selection(
    *,
    state: ConversationState,
    task: ConversationTask,
    parent_kind: ParentKind,
    user_text: str,
) -> DependencySearchResult:
    raw_candidates = task.snapshot.data.get(
        "incident_selection_candidates",
        [],
    )

    try:
        candidates = [
            SearchIncidentCandidate.model_validate(item)
            for item in raw_candidates
        ]
    except Exception as exc:
        raise DependencySearchError(
            "Search incident selection context is invalid."
        ) from exc

    interaction = task.pending_interaction

    if interaction is None:
        raise DependencySearchError(
            "Search incident selection has no pending interaction."
        )

    try:
        answer = await interpret_incident_answer(
            question=interaction.question,
            candidates=candidates,
            user_text=user_text,
        )
    except IncidentSelectionError:
        return DependencySearchResult(
            update=user_message(
                "Не удалось определить выбранный инцидент. "
                "Ответьте номером варианта или номером инцидента."
            )
        )

    if answer.action == "unclear":
        retry_interaction = build_interaction(
            owner=task.kind,
            continuation_stage=(
                "search.dependency.await_incident_selection"
            ),
            kind="single_select",
            question=answer.question or (
                "Уточните, какой инцидент выбрать."
            ),
            options=interaction.options,
            metadata=interaction.metadata,
        )

        from app.ai.runtime.task_lifecycle import set_awaiting_input

        lifecycle_update = set_awaiting_input(
            state,
            interaction=retry_interaction,
            stage=retry_interaction.continuation_stage,
        )

        return DependencySearchResult(
            update=merge_state_updates(
                lifecycle_update,
                render_interaction_text(retry_interaction)
            )
        )

    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate.entity_id == answer.entity_id
        ),
        None,
    )

    if selected is None:
        return DependencySearchResult(
            update=user_message(
                "Выбранный инцидент не найден среди вариантов."
            )
        )

    return DependencySearchResult(
        update={},
        incident_ref=IncidentRef(
            id=selected.entity_id,
            number=selected.number,
            label=selected.label,
        ),
    )