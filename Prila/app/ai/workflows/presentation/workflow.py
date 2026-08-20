from __future__ import annotations

from typing import Any

from app.ai.runtime.interaction_factory import build_interaction
from app.ai.runtime.services import get_memory
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
    utc_now_iso,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.presentation.collector_agent import (
    PresentationCollectionError,
    collect_presentation,
    extract_answer_update,
)
from app.ai.workflows.presentation.confirmation_agent import (
    interpret_presentation_confirmation,
)
from app.ai.workflows.presentation.contracts import PresentationSource
from app.ai.workflows.presentation.source_adapter import (
    PresentationSourceError,
    build_presentation_preview,
    load_description_source,
    load_incident_source,
    load_report_source,
    merge_presentation_fields,
)
from app.ai.workflows.registry import register_workflow
from app.ai.workflows.search.dependency import (
    DependencySearchResult,
    refine_dependency_search,
    resume_dependency_search,
    start_dependency_search,
)
from app.ai.workflows.search.presentation import render_interaction_text
from app.ai.workflows.updates import (
    merge_state_updates,
    status_message,
    user_message,
)
from app.memory.artifacts.presentations.document import PresentationDocument


@register_workflow("presentation")
def create_presentation_workflow() -> "PresentationWorkflow":
    return PresentationWorkflow()


class PresentationWorkflow:
    """
    Создаёт draft-презентацию по incident, RCA report, описанию или
    dependency Search.

    Persistence выполняется только после явного confirm preview.
    """

    async def start(
        self,
        state: ConversationState,
        task: ConversationTask,
    ) -> dict[str, Any]:
        plan = _load_plan(task)

        if plan.intent != "presentation":
            return _finish(
                state=state,
                message="Не удалось подготовить задачу создания презентации.",
            )

        if plan.requires_search:
            result = await start_dependency_search(
                state=state,
                task=task,
                parent_kind="presentation",
                query=plan.search_query or task.goal,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        try:
            source = await _load_source(
                state=state,
                plan=plan,
            )
        except PresentationSourceError as exc:
            return _finish(
                state=state,
                message=str(exc),
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось загрузить источник данных для презентации."
                ),
            )

        return await self._collect(
            state=state,
            task=task,
            source=source,
            collected_fields={},
            user_request=task.goal,
        )

    async def resume(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict[str, Any]:
        stage = task.snapshot.stage

        if stage.startswith("search.dependency."):
            result = await resume_dependency_search(
                state=state,
                task=task,
                parent_kind="presentation",
                user_text=user_text,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        if stage == "presentation.await_clarification":
            return await self._resume_clarification(
                state=state,
                task=task,
                user_text=user_text,
            )

        if stage == "presentation.await_confirmation":
            return await self._resume_confirmation(
                state=state,
                task=task,
                user_text=user_text,
            )

        return user_message(
            "Не удалось определить этап создания презентации."
        )

    async def continue_task(
        self,
        state: ConversationState,
        task: ConversationTask,
        *,
        user_text: str,
    ) -> dict[str, Any]:
        if task.snapshot.stage.startswith("search.dependency."):
            result = await refine_dependency_search(
                state=state,
                task=task,
                parent_kind="presentation",
                user_text=user_text,
                goal_hint=None,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        if task.snapshot.stage == "presentation.await_confirmation":
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
    ) -> dict[str, Any]:
        if task.snapshot.stage.startswith("search.dependency."):
            result = await refine_dependency_search(
                state=state,
                task=task,
                parent_kind="presentation",
                user_text=user_text,
                goal_hint=goal_hint,
            )

            return await self._continue_after_search(
                state=state,
                task=task,
                result=result,
            )

        if task.snapshot.stage == "presentation.await_confirmation":
            return user_message(
                "Сначала уточните, что изменить в preview, "
                "или подтвердите сохранение текущего варианта."
            )

        return await self._resume_clarification(
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
    ) -> dict[str, Any]:
        if not result.is_complete:
            return result.update

        incident_ref = result.incident_ref

        if incident_ref is None:
            return user_message(
                "Не удалось определить инцидент для презентации."
            )

        try:
            source = await load_incident_source(
                incident_ref=incident_ref,
            )
        except PresentationSourceError as exc:
            return _finish(
                state=state,
                message=str(exc),
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось загрузить найденный инцидент "
                    "для презентации."
                ),
            )

        stage_update = advance_stage(
            state,
            stage="presentation.collect",
            data={
                "presentation_source": source.model_dump(mode="json"),
                "collected_fields": {},
                "user_request": task.goal,
            },
            refs=[incident_ref],
        )

        next_state = {
            **state,
            **stage_update,
        }
        next_task = next_state["active_task"]

        if next_task is None:
            return user_message(
                "Не удалось продолжить создание презентации."
            )

        collected_update = await self._collect(
            state=next_state,
            task=next_task,
            source=source,
            collected_fields={},
            user_request=task.goal,
        )

        return merge_state_updates(
            result.update,
            stage_update,
            collected_update,
        )

    async def _collect(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        source: PresentationSource,
        collected_fields: dict[str, Any],
        user_request: str,
    ) -> dict[str, Any]:
        try:
            outcome = await collect_presentation(
                snapshot=task.snapshot,
                source=source,
                collected_fields=collected_fields,
                user_request=user_request,
            )
        except PresentationCollectionError:
            return _finish(
                state=state,
                message=(
                    "Не удалось собрать корректный документ презентации. "
                    "Попробуйте уточнить исходные данные."
                ),
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось собрать данные для презентации. "
                    "Попробуйте ещё раз позже."
                ),
            )

        if outcome.decision.action == "clarify":
            return _await_clarification(
                state=state,
                source=source,
                snapshot_data=outcome.snapshot_data,
                fields=outcome.decision.fields,
                missing_fields=outcome.decision.missing_fields,
                question=outcome.decision.question or (
                    "Уточните данные для презентации."
                ),
                user_request=user_request,
            )

        document = outcome.document

        if document is None:
            return _finish(
                state=state,
                message="Не удалось подготовить preview презентации.",
            )

        return _await_confirmation(
            state=state,
            source=source,
            snapshot_data=outcome.snapshot_data,
            document=document,
            user_request=user_request,
        )

    async def _resume_clarification(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict[str, Any]:
        try:
            source = PresentationSource.model_validate(
                task.snapshot.data["presentation_source"]
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось восстановить источник презентации. "
                    "Создайте её заново."
                ),
            )

        collected_fields = _dict_value(
            task.snapshot.data.get("collected_fields")
        )
        missing_fields = _string_list(
            task.snapshot.data.get("missing_fields")
        )
        question = str(
            task.snapshot.data.get("clarification_question") or ""
        ).strip()

        if not question:
            return _finish(
                state=state,
                message=(
                    "Не удалось восстановить вопрос по презентации. "
                    "Создайте её заново."
                ),
            )

        try:
            update = await extract_answer_update(
                snapshot=task.snapshot,
                question=question,
                missing_fields=missing_fields,
                collected_fields=collected_fields,
                user_text=user_text,
            )
        except Exception:
            return user_message(
                "Не удалось извлечь данные из ответа. "
                "Опишите их, пожалуйста, другими словами."
            )

        merged_fields = merge_presentation_fields(
            current=collected_fields,
            update=update.fields_update,
        )

        resumed_task = task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={
                        "data": {
                            **update.snapshot_data,
                            "presentation_source": source.model_dump(
                                mode="json"
                            ),
                            "collected_fields": merged_fields,
                            "user_request": str(
                                task.snapshot.data.get("user_request")
                                or task.goal
                            ),
                        }
                    }
                )
            }
        )

        return await self._collect(
            state=state,
            task=resumed_task,
            source=source,
            collected_fields=merged_fields,
            user_request=str(
                task.snapshot.data.get("user_request") or task.goal
            ),
        )

    async def _resume_confirmation(
        self,
        *,
        state: ConversationState,
        task: ConversationTask,
        user_text: str,
    ) -> dict[str, Any]:
        try:
            document = PresentationDocument.model_validate(
                task.snapshot.data["presentation_document"]
            )
        except Exception:
            return _finish(
                state=state,
                message=(
                    "Не удалось восстановить черновик презентации. "
                    "Создайте её заново."
                ),
            )

        try:
            confirmed = await interpret_presentation_confirmation(
                document=document,
                user_text=user_text,
            )
        except ValueError as exc:
            return user_message(str(exc))
        except Exception:
            return user_message(
                "Не удалось обработать подтверждение. "
                "Ответьте, пожалуйста: сохранить или не сохранять."
            )

        if not confirmed:
            return _finish(
                state=state,
                message="Хорошо, черновик презентации не сохраняю.",
            )

        try:
            presentation_id = await get_memory().create_presentation(
                user_id=state["user_id"],
                thread_id=state["thread_id"],
                fields=document,
            )
        except Exception:
            return user_message(
                "Документ презентации готов, но сохранить черновик "
                "в хранилище не удалось. Попробуйте ещё раз."
            )

        artifact = _presentation_artifact(
            presentation_id=presentation_id,
            document=document,
            task_id=task.task_id,
        )

        artifact_update = {
            "artifacts": {
                **state["artifacts"],
                presentation_id: artifact,
            },
        }

        completed_state = {
            **state,
            **artifact_update,
        }

        download_url = (
            f"/api/v1/presentations/{presentation_id}/file"
            "?version=draft"
        )

        return merge_state_updates(
            artifact_update,
            complete_active(completed_state),
            status_message("Черновик презентации сохранён."),
            user_message(
                "Презентация готова. "
                f"[Открыть HTML draft]({download_url})."
            ),
        )


def _load_plan(
    task: ConversationTask,
) -> ConversationPlan:
    raw_plan = task.snapshot.data.get("plan")

    if not isinstance(raw_plan, dict):
        raise ValueError(
            "Presentation task has no ConversationPlan"
        )

    return ConversationPlan.model_validate(raw_plan)


async def _load_source(
    *,
    state: ConversationState,
    plan: ConversationPlan,
) -> PresentationSource:
    if (
        plan.target_ref is not None
        and plan.target_ref.kind == "incident_report"
    ):
        return _load_report_source_from_state(
            state=state,
            report_id=plan.target_ref.id,
        )

    if plan.use_current_report:
        current_id = state.get("current_artifact_id")

        if not current_id:
            raise PresentationSourceError(
                "Текущая RCA-справка не найдена."
            )

        return _load_report_source_from_state(
            state=state,
            report_id=current_id,
        )

    if plan.incident_number:
        incident_ref = IncidentRef(
            id=plan.incident_number,
            number=plan.incident_number,
            label=plan.incident_number,
        )

        return await load_incident_source(
            incident_ref=incident_ref,
        )

    if plan.raw_description:
        return load_description_source(
            raw_description=plan.raw_description,
        )

    raise PresentationSourceError(
        "Для презентации нужен номер инцидента, описание, "
        "RCA-справка или поиск."
    )


def _load_report_source_from_state(
    *,
    state: ConversationState,
    report_id: str,
) -> PresentationSource:
    artifact = state["artifacts"].get(report_id)

    if (
        artifact is None
        or artifact.get("kind") != "incident_report"
    ):
        raise PresentationSourceError(
            "Указанная RCA-справка недоступна."
        )

    sections = artifact["versions"][
        artifact["current_version"]
    ]["sections"]

    report_ref = IncidentReportRef(
        id=report_id,
        label="RCA-справка",
    )

    return load_report_source(
        report_ref=report_ref,
        report_sections=sections,
    )


def _await_clarification(
    *,
    state: ConversationState,
    source: PresentationSource,
    snapshot_data: dict[str, Any],
    fields: dict[str, Any],
    missing_fields: list[str],
    question: str,
    user_request: str,
) -> dict[str, Any]:
    interaction = build_interaction(
        owner="presentation",
        continuation_stage="presentation.await_clarification",
        kind="free_text",
        question=question,
        metadata={
            "purpose": "presentation_clarification",
            "missing_fields": missing_fields,
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
            "presentation_source": source.model_dump(mode="json"),
            "collected_fields": fields,
            "missing_fields": missing_fields,
            "clarification_question": question,
            "user_request": user_request,
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Собираю данные для презентации."),
        user_message(render_interaction_text(interaction)),
    )


def _await_confirmation(
    *,
    state: ConversationState,
    source: PresentationSource,
    snapshot_data: dict[str, Any],
    document: PresentationDocument,
    user_request: str,
) -> dict[str, Any]:
    preview = build_presentation_preview(document)

    interaction = build_interaction(
        owner="presentation",
        continuation_stage="presentation.await_confirmation",
        kind="confirm",
        question=preview,
        preview=document.model_dump(mode="json"),
        metadata={
            "purpose": "presentation_save_confirmation",
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
            "presentation_source": source.model_dump(mode="json"),
            "presentation_document": document.model_dump(
                mode="json"
            ),
            "user_request": user_request,
        },
    )

    return merge_state_updates(
        lifecycle_update,
        status_message("Подготовил preview презентации."),
        user_message(render_interaction_text(interaction)),
    )


def _presentation_artifact(
    *,
    presentation_id: str,
    document: PresentationDocument,
    task_id: str,
) -> dict[str, Any]:
    now = utc_now_iso()

    return {
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
                "produced_by_worker_id": task_id,
                "note": "Presentation draft stored in memory",
                "timestamp": now,
            }
        ],
        "current_version": 0,
        "locked_for_editing": False,
        "created_by_worker_id": task_id,
        "created_at": now,
    }


def _dict_value(
    value: Any,
) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(
    value: Any,
) -> list[str]:
    if not isinstance(value, list):
        return []

    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def _finish(
    *,
    state: ConversationState,
    message: str,
) -> dict[str, Any]:
    return merge_state_updates(
        complete_active(state),
        user_message(message),
    )
