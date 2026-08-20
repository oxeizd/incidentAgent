from __future__ import annotations

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
    IncidentRef,
    IncidentReportRef,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.rca.analyzer_agent import (
    AnalyzerOutcome,
    run_analyzer,
)
from app.ai.workflows.rca.context_loader import (
    RCAContextLoadError,
    load_from_description,
    load_from_incident,
)
from app.ai.workflows.rca.contracts import (
    RCAGateDecision,
    RCAInput,
)
from app.ai.workflows.rca.gate_agent import (
    GateOutcome,
    run_gate,
)
from app.ai.workflows.rca.report_adapter import (
    create_incident_report,
)
from app.ai.workflows.rca.task_validator_agent import (
    TaskValidationOutcome,
    validate_rca_tasks,
)
from app.ai.workflows.registry import register_workflow
from app.ai.workflows.search.dependency import (
    DependencySearchError,
    DependencySearchResult,
    refine_dependency_search,
    resume_dependency_search,
    start_dependency_search,
)
from app.ai.workflows.search.presentation import (
    render_interaction_text,
)
from app.ai.workflows.updates import (
    merge_state_updates,
    status_message,
    user_message,
)


_RCA_AGENT = "rca_workflow"
_MAX_CLARIFICATION_ROUNDS = 5


@register_workflow("rca")
def create_rca_workflow() -> "RCAWorkflow":
    return RCAWorkflow()


class RCAWorkflow:
    """
    Полный RCA workflow.

    Вход:
    - incident_number;
    - raw_description;
    - current incident_report для reanalysis;
    - dependency Search, если Planner поставил requires_search=true.

    Search, если нужен, остаётся внутренним этапом: task.kind не меняется
    с rca на search и пользователь не видит отдельного агента.
    """

    async def start(
        self,
        state: ConversationState,
        task: ConversationTask,
    ) -> dict:
        plan = _load_plan(task)

        if plan.intent != "rca":
            return user_message(
                "Не удалось подготовить RCA-задачу."
            )

        if plan.requires_search:
            query = plan.search_query or task.goal

            result = await start_dependency_search(
                state=state,
                task=task,
                parent_kind="rca",
                query=query,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        rca_input = await _load_rca_input(
            state=state,
            task=task,
            plan=plan,
        )

        return await self._run_gate(
            state=state,
            task=task,
            rca_input=rca_input,
        )

    async def resume(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        stage = task.snapshot.stage

        if stage.startswith("search.dependency."):
            result = await resume_dependency_search(
                state=state,
                task=task,
                parent_kind="rca",
                user_text=user_text,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        if stage == "rca.await_clarification":
            return await self._resume_gate(
                state=state,
                task=task,
                user_text=user_text,
            )

        return user_message(
            "Не удалось определить этап RCA для продолжения."
        )

    async def continue_task(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        """
        Продолжение active RCA без pending interaction.

        Пока считаем такое сообщение новым evidence для текущего RCA:
        добавляем его в evidence и снова проверяем gate, но не создаём
        новый RCA task и не теряем исходный context.
        """
        if task.snapshot.stage.startswith("search.dependency."):
            result = await refine_dependency_search(
                state=state,
                task=task,
                parent_kind="rca",
                user_text=user_text,
                goal_hint=None,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        return await self._append_evidence_and_run_gate(
            state=state,
            task=task,
            user_text=user_text,
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
        При изменении параметров поиска refinement остаётся в Search stage.
        После начала RCA любой новый фактический контекст передаём Gate как
        evidence: он решит, нужны ли повторные вопросы или можно анализировать.
        """
        if task.snapshot.stage.startswith("search.dependency."):
            result = await refine_dependency_search(
                state=state,
                task=task,
                parent_kind="rca",
                user_text=user_text,
                goal_hint=goal_hint,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        return await self._append_evidence_and_run_gate(
            state=state,
            task=task,
            user_text=user_text,
        )

    async def _continue_after_search(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        result: DependencySearchResult,
    ) -> dict:
        """
        Если Search ещё ждёт user input — возвращает его update.

        Когда selected IncidentRef готов, строит RCAInput и запускает gate.
        Важно: RCA получает ref, а не SearchResult/preview rows.
        """
        if not result.is_complete:
            return result.update

        rca_input = await _load_incident_input(
            incident_ref=result.incident_ref,
        )

        search_data = append_agent_event(
            task.snapshot.data,
            agent=_RCA_AGENT,
            role="system",
            kind="selected_incident",
            payload={
                "incident_ref": result.incident_ref.model_dump(
                    mode="json"
                )
            },
        )

        search_stage_update = advance_stage(
            state,
            stage="rca.load_context",
            data={
                "agent_history": (
                    search_data.get("agent_history") or {}
                ),
                "rca_input": rca_input.model_dump(mode="json"),
            },
            refs=[result.incident_ref],
        )

        next_state = {
            **state,
            **search_stage_update,
        }
        next_task = next_state["active_task"]

        if next_task is None:
            return user_message(
                "Не удалось продолжить RCA после выбора инцидента."
            )

        gate_update = await self._run_gate(
            state=next_state,
            task=next_task,
            rca_input=rca_input,
        )

        return merge_state_updates(
            result.update,
            search_stage_update,
            gate_update,
        )

    async def _run_gate(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        rca_input: RCAInput,
    ) -> dict:
        try:
            outcome = await run_gate(
                snapshot=task.snapshot,
                rca_input=rca_input,
            )
        except Exception:
            return user_message(
                "Не удалось проверить данные для RCA. "
                "Попробуйте ещё раз позже."
            )

        decision = outcome.decision

        if decision.action == "stop":
            return _finish_with_message(
                state=state,
                message=(
                    "Не могу подготовить RCA по текущим данным: "
                    f"{decision.reason}"
                ),
            )

        if decision.action == "clarify":
            return _await_rca_clarification(
                state=state,
                task=task,
                rca_input=rca_input,
                outcome=outcome,
            )

        return await self._run_analysis(
            state=state,
            task=task,
            rca_input=rca_input,
            gate=decision,
            gate_snapshot_data=outcome.snapshot_data,
        )

    async def _resume_gate(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict:
        raw_input = task.snapshot.data.get("rca_input")

        try:
            rca_input = RCAInput.model_validate(raw_input)
        except Exception:
            return user_message(
                "Не удалось восстановить контекст RCA. "
                "Начните анализ заново."
            )

        updated_input = rca_input.model_copy(
            update={
                "user_evidence": [
                    *rca_input.user_evidence,
                    user_text.strip(),
                ]
            }
        )

        data = append_agent_event(
            task.snapshot.data,
            agent=_RCA_AGENT,
            role="user",
            kind="clarification_answer",
            payload={"text": user_text.strip()},
        )

        data = {
            **data,
            "rca_input": updated_input.model_dump(mode="json"),
        }

        resumed_task = task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"data": data}
                )
            }
        )

        checkpoint_update = advance_stage(
            state,
            stage="rca.gate",
            data={
                "agent_history": (
                    data.get("agent_history") or {}
                ),
                "rca_input": updated_input.model_dump(
                    mode="json"
                ),
            },
        )

        checkpoint_state = {
            **state,
            **checkpoint_update,
        }

        checkpoint_task = checkpoint_state["active_task"]

        if checkpoint_task is None:
            return user_message(
                "Не удалось сохранить контекст RCA."
            )

        return await self._run_gate(
            state=checkpoint_state,
            task=checkpoint_task,
            rca_input=updated_input,
        )

    async def _append_evidence_and_run_gate(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict:
        raw_input = task.snapshot.data.get("rca_input")

        try:
            rca_input = RCAInput.model_validate(raw_input)
        except Exception:
            return user_message(
                "Не удалось восстановить контекст RCA."
            )

        updated_input = rca_input.model_copy(
            update={
                "user_evidence": [
                    *rca_input.user_evidence,
                    user_text.strip(),
                ]
            }
        )

        data = append_agent_event(
            task.snapshot.data,
            agent=_RCA_AGENT,
            role="user",
            kind="additional_evidence",
            payload={"text": user_text.strip()},
        )

        data = {
            **data,
            "rca_input": updated_input.model_dump(mode="json"),
        }

        updated_task = task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"data": data}
                )
            }
        )

        checkpoint_update = advance_stage(
            state,
            stage="rca.gate",
            data={
                "agent_history": (
                    data.get("agent_history") or {}
                ),
                "rca_input": updated_input.model_dump(
                    mode="json"
                ),
            },
        )

        checkpoint_state = {
            **state,
            **checkpoint_update,
        }

        checkpoint_task = checkpoint_state["active_task"]

        if checkpoint_task is None:
            return user_message(
                "Не удалось сохранить контекст RCA."
            )

        return await self._run_gate(
            state=checkpoint_state,
            task=checkpoint_task,
            rca_input=updated_input,
        )

    async def _run_analysis(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        rca_input: RCAInput,
        gate: RCAGateDecision,
        gate_snapshot_data: dict[str, Any],
    ) -> dict:
        gate_task = task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={
                        "data": gate_snapshot_data,
                    }
                )
            }
        )

        try:
            analyzer = await run_analyzer(
                snapshot=gate_task.snapshot,
                rca_input=rca_input,
                gate=gate,
            )
        except Exception:
            return user_message(
                "Не удалось сформировать RCA-анализ. "
                "Попробуйте повторить запрос."
            )

        analyzer_task = gate_task.model_copy(
            update={
                "snapshot": gate_task.snapshot.model_copy(
                    update={
                        "data": analyzer.snapshot_data,
                    }
                )
            }
        )

        try:
            validation = await validate_rca_tasks(
                snapshot=analyzer_task.snapshot,
                corrective_actions=analyzer.draft.corrective_actions,
                preventive_actions=analyzer.draft.preventive_actions,
            )
        except Exception:
            return user_message(
                "RCA-анализ подготовлен, но проверить системные меры "
                "сейчас не удалось."
            )

        return self._persist_report(
            state=state,
            task=analyzer_task,
            rca_input=rca_input,
            gate=gate,
            analyzer=analyzer,
            validation=validation,
        )

    def _persist_report(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        rca_input: RCAInput,
        gate: RCAGateDecision,
        analyzer: AnalyzerOutcome,
        validation: TaskValidationOutcome,
    ) -> dict:
        artifact, report_ref = create_incident_report(
            task_id=task.task_id,
            rca_input=rca_input,
            gate=gate,
            draft=analyzer.draft,
            validation=validation.validation,
        )

        data = append_agent_event(
            validation.snapshot_data,
            agent=_RCA_AGENT,
            role="assistant",
            kind="report_created",
            payload={
                "report_ref": report_ref.model_dump(
                    mode="json"
                )
            },
        )

        artifact_update = {
            "artifacts": {
                **state["artifacts"],
                artifact["id"]: artifact,
            },
            "current_artifact_id": artifact["id"],
        }

        stage_update = advance_stage(
            state,
            stage="rca.completed",
            data={
                "agent_history": (
                    data.get("agent_history") or {}
                ),
                "rca_input": rca_input.model_dump(mode="json"),
                "report_ref": report_ref.model_dump(mode="json"),
            },
            refs=[report_ref],
        )

        completed_state = {
            **state,
            **stage_update,
        }

        completion_update = complete_active(completed_state)

        return merge_state_updates(
            stage_update,
            artifact_update,
            completion_update,
            status_message(
                "Справка по инциденту готова."
            ),
            user_message(
                _render_completed_report(
                    analysis=analyzer.draft.analysis,
                    accepted_count=len(
                        validation.validation.accepted_tasks
                    ),
                    rejected_count=len(
                        validation.validation.rejected_tasks
                    ),
                )
            ),
        )


def _load_plan(
    task: ConversationTask,
) -> ConversationPlan:
    raw = task.snapshot.data.get("plan")

    if not isinstance(raw, dict):
        raise ValueError(
            "RCA task has no ConversationPlan"
        )

    return ConversationPlan.model_validate(raw)


async def _load_rca_input(
    *,
    state: ConversationState,
    task: ConversationTask,
    plan: ConversationPlan,
) -> RCAInput:
    """
    Выбирает source без LLM эвристик: source уже определён Planner-ом.

    Если Planner не смог дать source и не поставил requires_search=true,
    это invalid plan, а не повод Python брать последнюю user message как
    произвольное описание.
    """
    if plan.target_ref and plan.target_ref.kind == "incident":
        incident_ref = IncidentRef.model_validate(
            plan.target_ref.model_dump(mode="json")
        )
        return await _load_incident_input(
            incident_ref=incident_ref,
        )

    if plan.incident_number:
        incident_ref = IncidentRef(
            id=plan.incident_number,
            number=plan.incident_number,
            label=plan.incident_number,
        )
        return await _load_incident_input(
            incident_ref=incident_ref,
        )

    if plan.raw_description:
        return load_from_description(
            raw_description=plan.raw_description,
        )

    raise RCAContextLoadError(
        "Для RCA нужен номер инцидента, описание или поиск."
    )


async def _load_incident_input(
    *,
    incident_ref: IncidentRef,
) -> RCAInput:
    return await load_from_incident(
        incident_ref=incident_ref,
    )


def _await_rca_clarification(
    *,
    state: ConversationState,
    task: ConversationTask,
    rca_input: RCAInput,
    outcome: GateOutcome,
) -> dict:
    decision = outcome.decision

    questions = [
        question.strip()
        for question in decision.questions
        if question and question.strip()
    ]

    if not questions:
        return _finish_with_message(
            state=state,
            message=(
                "Для RCA нужны дополнительные данные, но не удалось "
                "сформулировать уточняющий вопрос."
            ),
        )

    if task.snapshot.data.get("rca_clarification_rounds", 0) >= (
        _MAX_CLARIFICATION_ROUNDS
    ):
        return _finish_with_message(
            state=state,
            message=(
                "Не удалось собрать достаточно данных для RCA "
                "за отведённое число уточнений."
            ),
        )

    question_text = "\n".join(
        [
            "Для продолжения RCA уточните:",
            *[
                f"{index}. {question}"
                for index, question in enumerate(
                    questions,
                    start=1,
                )
            ],
        ]
    )

    interaction = build_interaction(
        owner="rca",
        continuation_stage="rca.await_clarification",
        kind="free_text",
        question=question_text,
        metadata={
            "purpose": "rca_clarification",
            "question_count": len(questions),
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
            "rca_input": rca_input.model_dump(mode="json"),
            "rca_clarification_rounds": (
                task.snapshot.data.get(
                    "rca_clarification_rounds",
                    0,
                )
                + 1
            ),
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Проверяю данные для RCA."),
        user_message(render_interaction_text(interaction)),
    )


def _finish_with_message(
    *,
    state: ConversationState,
    message: str,
) -> dict:
    completion_update = complete_active(state)

    return merge_state_updates(
        completion_update,
        user_message(message),
    )


def _render_completed_report(
    *,
    analysis: str,
    accepted_count: int,
    rejected_count: int,
) -> str:
    lines = [
        analysis.strip(),
        "",
        "---",
        "",
        (
            f"Системных мер в справке: {accepted_count}."
        ),
    ]

    if rejected_count:
        lines.append(
            (
                "Не включено неконкретных или непроверяемых мер: "
                f"{rejected_count}."
            )
        )

    return "\n".join(lines)