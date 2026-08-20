from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.ai.runtime.interaction_factory import build_interaction
from app.ai.runtime.task_lifecycle import (
    advance_stage,
    cancel_active,
    complete_active,
    set_awaiting_input,
)
from app.ai.schemas.artifact import SectionPatch
from app.ai.schemas.conversation import (
    ConversationPlan,
    ConversationTask,
    IncidentReportRef,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.editor.agents import (
    EditorIntentOutcome,
    EditorProposalOutcome,
    run_editor_intent,
    run_editor_proposal,
)
from app.ai.workflows.editor.contracts import (
    EditIntentDecision,
    EditProposal,
)
from app.ai.workflows.rca.contracts import (
    ProposedTask,
    ValidatedTask,
)
from app.ai.workflows.rca.task_validator_agent import (
    validate_rca_tasks,
)
from app.ai.workflows.registry import register_workflow
from app.ai.workflows.search.presentation import (
    render_interaction_text,
)
from app.ai.workflows.updates import (
    merge_state_updates,
    status_message,
    user_message,
)
from app.ai.runtime.artifacts import (
    apply_patches_to_artifact,
    replace_artifact_sections,
)


@register_workflow("edit")
def create_editor_workflow() -> "EditorWorkflow":
    return EditorWorkflow()


class EditorWorkflow:
    """
    Controlled edit versioned RCA report.

    Никаких side effects до user confirmation:
    - intent/proposal только формируют preview;
    - confirm сохраняет artifact новую версию;
    - task add/replace проходит повторную task validation.
    """

    async def start(
        self,
        state: ConversationState,
        task: ConversationTask,
    ) -> dict:
        plan = _load_plan(task)

        report = _resolve_report(
            state=state,
            plan=plan,
        )

        if report is None:
            return _finish(
                state=state,
                message=(
                    "Не нашёл RCA-справку для редактирования. "
                    "Сначала подготовьте RCA."
                ),
            )

        artifact_id, artifact = report
        sections = _current_sections(artifact)

        try:
            intent = await run_editor_intent(
                snapshot=task.snapshot,
                report_summary=_report_summary(sections),
                tasks=_tasks(sections),
                user_text=plan.edit_instruction or task.goal,
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось определить параметры правки. "
                    "Попробуйте сформулировать изменение иначе."
                ),
            )

        return await self._handle_intent(
            state=state,
            task=task,
            artifact_id=artifact_id,
            artifact=artifact,
            sections=sections,
            outcome=intent,
        )

    async def resume(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        stage = task.snapshot.stage

        if stage == "editor.await_clarification":
            return await self._resume_clarification(
                state=state,
                task=task,
                user_text=user_text,
            )

        if stage == "editor.await_confirmation":
            return await self._resume_confirmation(
                state=state,
                task=task,
                user_text=user_text,
            )

        return user_message(
            "Не удалось определить этап редактирования."
        )

    async def continue_task(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict:
        """
        Дополнительная реплика в active Editor task трактуется как уточнение
        исходной инструкции и запускает intent заново, пока нет preview.
        """
        if task.snapshot.stage == "editor.await_confirmation":
            return await self._resume_confirmation(
                state=state,
                task=task,
                user_text=user_text,
            )

        return await self._resume_clarification(
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
        return await self._resume_clarification(
            state=state,
            task=task,
            user_text=user_text,
        )

    async def _handle_intent(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        artifact_id: str,
        artifact: dict[str, Any],
        sections: dict[str, Any],
        outcome: EditorIntentOutcome,
    ) -> dict:
        decision = outcome.decision

        if decision.action == "clarify":
            return _await_clarification(
                state=state,
                task=task,
                artifact_id=artifact_id,
                snapshot_data=outcome.snapshot_data,
                question=decision.question or (
                    "Уточните, что нужно изменить в справке."
                ),
            )

        target_content = _target_content(
            sections=sections,
            decision=decision,
        )

        if target_content is None:
            return _await_clarification(
                state=state,
                task=task,
                artifact_id=artifact_id,
                snapshot_data=outcome.snapshot_data,
                question=(
                    "Не нашёл указанную часть справки. "
                    "Уточните, что именно нужно изменить."
                ),
            )

        try:
            proposal = await run_editor_proposal(
                snapshot=task.snapshot.model_copy(
                    update={"data": outcome.snapshot_data}
                ),
                intent=decision,
                target_content=target_content,
                rca_context=_rca_context(sections),
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось подготовить preview правки. "
                    "Попробуйте уточнить инструкцию."
                ),
            )

        if not _proposal_matches_intent(
            proposal=proposal.proposal,
            intent=decision,
            sections=sections,
        ):
            return _finish(
                state=state,
                message=(
                    "Не удалось безопасно подготовить правку: preview "
                    "не соответствует выбранному разделу."
                ),
            )

        return _await_confirmation(
            state=state,
            task=task,
            artifact_id=artifact_id,
            snapshot_data=proposal.snapshot_data,
            proposal=proposal.proposal,
        )

    async def _resume_clarification(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict:
        artifact_id = task.snapshot.data.get("artifact_id")

        if not isinstance(artifact_id, str):
            return _finish(
                state=state,
                message=(
                    "Не удалось восстановить RCA-справку "
                    "для редактирования."
                ),
            )

        artifact = state["artifacts"].get(artifact_id)

        if artifact is None:
            return _finish(
                state=state,
                message="RCA-справка больше недоступна.",
            )

        sections = _current_sections(artifact)

        try:
            intent = await run_editor_intent(
                snapshot=task.snapshot,
                report_summary=_report_summary(sections),
                tasks=_tasks(sections),
                user_text=user_text,
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось понять уточнение. "
                    "Опишите нужную правку иначе."
                ),
            )

        return await self._handle_intent(
            state=state,
            task=task,
            artifact_id=artifact_id,
            artifact=artifact,
            sections=sections,
            outcome=intent,
        )

    async def _resume_confirmation(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict:
        """
        Text confirmation интерпретирует LLM, а не набор Python keywords.

        Для этого нужен маленький confirmation agent. Пока его контракт и
        агент будут добавлены следующим файлом; здесь вызываем helper,
        который появится в app.ai.workflows.editor.confirmation_agent.
        """
        from app.ai.workflows.editor.confirmation_agent import (
            interpret_editor_confirmation,
        )

        raw_proposal = task.snapshot.data.get("edit_proposal")
        artifact_id = task.snapshot.data.get("artifact_id")

        try:
            proposal = EditProposal.model_validate(raw_proposal)
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось восстановить preview правки. "
                    "Создайте правку заново."
                ),
            )

        if not isinstance(artifact_id, str):
            return _finish(
                state=state,
                message="Не удалось найти RCA-справку для сохранения.",
            )

        try:
            confirmed = await interpret_editor_confirmation(
                proposal=proposal,
                user_text=user_text,
            )
        except ValueError as exc:
            return user_message(str(exc))
        except Exception:
            return user_message(
                "Не удалось обработать подтверждение. "
                "Ответьте, пожалуйста: «да» или «нет»."
            )

        if not confirmed:
            return _finish(
                state=state,
                message="Хорошо, правку не применяю.",
            )

        artifact = state["artifacts"].get(artifact_id)

        if artifact is None:
            return _finish(
                state=state,
                message="RCA-справка больше недоступна.",
            )

        try:
            updated_artifact = await _apply_confirmed_proposal(
                artifact=artifact,
                proposal=proposal,
                task=task,
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось применить правку к RCA-справке."
                ),
            )

        artifact_update = {
            "artifacts": {
                **state["artifacts"],
                artifact_id: updated_artifact,
            },
            "current_artifact_id": artifact_id,
        }

        completed_state = {
            **state,
            **artifact_update,
        }

        return merge_state_updates(
            artifact_update,
            complete_active(completed_state),
            status_message("Правка сохранена в новой версии справки."),
            user_message(
                "Готово. Создал новую версию RCA-справки."
            ),
        )


def _load_plan(
    task: ConversationTask,
) -> ConversationPlan:
    raw = task.snapshot.data.get("plan")

    if not isinstance(raw, dict):
        raise ValueError("Editor task has no ConversationPlan")

    return ConversationPlan.model_validate(raw)


def _resolve_report(
    *,
    state: ConversationState,
    plan: ConversationPlan,
) -> tuple[str, dict[str, Any]] | None:
    target_ref = plan.target_ref

    if (
        target_ref is not None
        and target_ref.kind == "incident_report"
    ):
        artifact = state["artifacts"].get(target_ref.id)

        if artifact is not None:
            return target_ref.id, artifact

    current_id = state.get("current_artifact_id")

    if not current_id:
        return None

    artifact = state["artifacts"].get(current_id)

    if (
        artifact is None
        or artifact.get("kind") != "incident_report"
    ):
        return None

    return current_id, artifact


def _current_sections(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    return artifact["versions"][
        artifact["current_version"]
    ]["sections"]


def _tasks(
    sections: dict[str, Any],
) -> list[dict[str, Any]]:
    value = sections.get("tasks") or []

    return [
        task
        for task in value
        if isinstance(task, dict)
    ]


def _report_summary(
    sections: dict[str, Any],
) -> dict[str, Any]:
    return {
        "summary": sections.get("summary"),
        "root_cause": sections.get("root_cause"),
        "impact": sections.get("impact"),
        "timeline": sections.get("timeline"),
        "open_questions": sections.get("open_questions"),
        "limitations": sections.get("limitations"),
    }


def _rca_context(
    sections: dict[str, Any],
) -> dict[str, Any]:
    return {
        "root_cause": sections.get("root_cause"),
        "root_cause_kind": sections.get("root_cause_kind"),
        "causal_chain": sections.get("causal_chain") or [],
        "contributing_factors": (
            sections.get("contributing_factors") or []
        ),
        "facts": sections.get("facts") or [],
        "limitations": sections.get("limitations") or [],
    }


def _target_content(
    *,
    sections: dict[str, Any],
    decision: EditIntentDecision,
) -> Any | None:
    if decision.action == "edit_text":
        return sections.get(decision.section)

    if decision.action == "edit_task":
        tasks = _tasks(sections)

        if decision.task_operation == "add":
            return {"tasks": tasks}

        index = decision.task_index

        if index is None or index >= len(tasks):
            return None

        return {
            "task_index": index,
            "task": tasks[index],
        }

    return None


def _proposal_matches_intent(
    *,
    proposal: EditProposal,
    intent: EditIntentDecision,
    sections: dict[str, Any],
) -> bool:
    """
    Boundary validation: LLM может предложить только тот тип edit, который
    определён intent agent-ом, и только существующую task/section.
    """
    if intent.action == "edit_text":
        return (
            proposal.kind == "text"
            and proposal.text_change is not None
            and proposal.text_change.section == intent.section
        )

    if intent.action == "edit_task":
        change = proposal.task_change

        if proposal.kind != "task" or change is None:
            return False

        if change.operation != intent.task_operation:
            return False

        if change.operation == "add":
            return change.task_index is None

        if change.task_index != intent.task_index:
            return False

        tasks = _tasks(sections)

        if (
            change.task_index is None
            or change.task_index >= len(tasks)
        ):
            return False

        return change.original_task == tasks[change.task_index]

    return False


def _await_clarification(
    *,
    state: ConversationState,
    task: ConversationTask,
    artifact_id: str,
    snapshot_data: dict[str, Any],
    question: str,
) -> dict:
    interaction = build_interaction(
        owner="edit",
        continuation_stage="editor.await_clarification",
        kind="free_text",
        question=question,
        metadata={
            "purpose": "editor_clarification",
        },
    )

    lifecycle_update = set_awaiting_input(
        state,
        interaction=interaction,
        stage=interaction.continuation_stage,
        data={
            "agent_history": (
                snapshot_data.get("agent_history") or {}
            ),
            "artifact_id": artifact_id,
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Уточняю параметры правки."),
        user_message(render_interaction_text(interaction)),
    )


def _await_confirmation(
    *,
    state: ConversationState,
    task: ConversationTask,
    artifact_id: str,
    snapshot_data: dict[str, Any],
    proposal: EditProposal,
) -> dict:
    preview = _render_proposal_preview(proposal)

    interaction = build_interaction(
        owner="edit",
        continuation_stage="editor.await_confirmation",
        kind="confirm",
        question=(
            f"{preview}\n\n"
            "Применить эту правку и создать новую версию справки?"
        ),
        preview=proposal.model_dump(mode="json"),
        metadata={
            "purpose": "editor_save_confirmation",
            "artifact_id": artifact_id,
        },
    )

    lifecycle_update = set_awaiting_input(
        state,
        interaction=interaction,
        stage=interaction.continuation_stage,
        data={
            "agent_history": (
                snapshot_data.get("agent_history") or {}
            ),
            "artifact_id": artifact_id,
            "edit_proposal": proposal.model_dump(mode="json"),
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Подготавливаю preview правки."),
        user_message(render_interaction_text(interaction)),
    )


async def _apply_confirmed_proposal(
    *,
    artifact: dict[str, Any],
    proposal: EditProposal,
    task: ConversationTask,
) -> dict[str, Any]:
    if proposal.kind == "text":
        change = proposal.text_change

        if change is None:
            raise ValueError("Missing text change")

        patch = SectionPatch(
            section=change.section,
            original_value=change.original_value,
            new_value=change.new_value,
            operation="update",
            applied_by_worker_id=task.task_id,
            timestamp=task.updated_at,
            note=change.rationale,
        )

        return apply_patches_to_artifact(
            artifact,
            [patch],
            note=change.rationale,
        )

    change = proposal.task_change

    if change is None:
        raise ValueError("Missing task change")

    tasks = deepcopy(_tasks(_current_sections(artifact)))

    if change.operation == "add":
        if change.new_task is None:
            raise ValueError("Task add has no new_task")

        validated = await validate_rca_tasks(
            snapshot=task.snapshot,
            corrective_actions=[change.new_task],
            preventive_actions=[],
        )

        if not validated.validation.accepted_tasks:
            raise ValueError("Task add was rejected by validator")

        tasks.append(
            validated.validation.accepted_tasks[0].model_dump(
                mode="json"
            )
        )

    elif change.operation == "replace":
        if (
            change.task_index is None
            or change.new_task is None
            or change.task_index >= len(tasks)
        ):
            raise ValueError("Invalid task replace")

        validated = await validate_rca_tasks(
            snapshot=task.snapshot,
            corrective_actions=[change.new_task],
            preventive_actions=[],
        )

        if not validated.validation.accepted_tasks:
            raise ValueError("Task replace was rejected by validator")

        tasks[change.task_index] = (
            validated.validation.accepted_tasks[0].model_dump(
                mode="json"
            )
        )

    elif change.operation == "remove":
        if (
            change.task_index is None
            or change.task_index >= len(tasks)
        ):
            raise ValueError("Invalid task remove")

        tasks.pop(change.task_index)

    else:
        raise ValueError(
            f"Unsupported task operation: {change.operation!r}"
        )

    return replace_artifact_sections(
        artifact,
        {"tasks": tasks},
        produced_by_worker_id=task.task_id,
        note=change.rationale,
    )


def _render_proposal_preview(
    proposal: EditProposal,
) -> str:
    if proposal.kind == "text":
        change = proposal.text_change

        if change is None:
            return "Preview правки недоступен."

        return (
            f"### Раздел: {change.section}\n\n"
            f"**Было:**\n{change.original_value}\n\n"
            f"**Станет:**\n{change.new_value}\n\n"
            f"_Основание: {change.rationale}_"
        )

    change = proposal.task_change

    if change is None:
        return "Preview правки недоступен."

    if change.operation == "remove":
        return (
            "### Удаление системной меры\n\n"
            f"**Будет удалено:**\n"
            f"{change.original_task}\n\n"
            f"_Основание: {change.rationale}_"
        )

    return (
        f"### Изменение системной меры: {change.operation}\n\n"
        f"**Было:**\n{change.original_task or '—'}\n\n"
        f"**Станет:**\n"
        f"{change.new_task.model_dump(mode='json') if change.new_task else '—'}"
        f"\n\n"
        f"_Основание: {change.rationale}_"
    )


def _finish(
    *,
    state: ConversationState,
    message: str,
) -> dict:
    return merge_state_updates(
        complete_active(state),
        user_message(message),
    )