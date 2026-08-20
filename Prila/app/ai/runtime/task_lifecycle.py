from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.ai.schemas.conversation import (
    ConversationTask,
    DomainRef,
    Interaction,
    TaskKind,
    TaskSnapshot,
)
from app.ai.schemas.conversation_state import ConversationState


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id(kind: TaskKind) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"


class TaskLifecycleError(Exception):
    """
    Нарушение инварианта lifecycle.

    Это программная ошибка вызывающего кода (Guard/Planner/workflow), а не
    пользовательская ошибка. Она не должна доходить до пользователя как есть.
    """


def start_task(
    state: ConversationState,
    *,
    kind: TaskKind,
    goal: str,
    initial_stage: str,
    initial_data: dict[str, Any] | None = None,
    refs: list[DomainRef] | None = None,
) -> dict[str, Any]:
    """
    Создаёт новую active_task.

    Вызывающий код обязан заранее решить, что делать со старой active_task
    (suspend_active перед вызовом), иначе бросаем ошибку — молчаливая потеря
    задачи запрещена требованиями.
    """
    if state["active_task"] is not None:
        raise TaskLifecycleError(
            "Cannot start a new task while active_task is set. "
            "Call suspend_active() or cancel_active() first."
        )

    now = utc_now_iso()

    task = ConversationTask(
        task_id=new_task_id(kind),
        kind=kind,
        goal=goal,
        status="active",
        snapshot=TaskSnapshot(
            stage=initial_stage,
            data=initial_data or {},
        ),
        refs=list(refs or []),
        pending_interaction=None,
        created_at=now,
        updated_at=now,
    )

    return {"active_task": task}


def suspend_active(
    state: ConversationState,
    *,
    reason: str,
) -> dict[str, Any]:
    """
    Приостанавливает текущую active_task и делает её единственной
    suspended_task.

    Ранее отложенная задача (если была) замещается — это осознанное решение
    по требованиям: система хранит максимум одну отложенную задачу, вторую
    явно теряем, а не копим стек.
    """
    active = state["active_task"]

    if active is None:
        return {}

    now = utc_now_iso()

    suspended = active.model_copy(
        update={
            "status": "suspended",
            "suspended_at": now,
            "suspension_reason": reason,
            "updated_at": now,
        }
    )

    return {
        "active_task": None,
        "suspended_task": suspended,
    }


def switch_to_suspended(
    state: ConversationState,
    *,
    reason: str,
) -> dict[str, Any]:
    """
    Активирует suspended_task.

    Если есть active_task, она становится новой suspended_task, а прежняя
    suspended_task — active. Это единственный безопасный swap при лимите
    «одна активная + одна отложенная».
    """
    target = state["suspended_task"]

    if target is None:
        raise TaskLifecycleError(
            "No suspended_task to resume."
        )

    now = utc_now_iso()
    active = state["active_task"]

    restored = target.model_copy(
        update={
            "status": "active",
            "suspended_at": None,
            "suspension_reason": None,
            "updated_at": now,
        }
    )

    if active is None:
        return {
            "active_task": restored,
            "suspended_task": None,
        }

    replacement = active.model_copy(
        update={
            "status": "suspended",
            "suspended_at": now,
            "suspension_reason": reason,
            "updated_at": now,
        }
    )

    return {
        "active_task": restored,
        "suspended_task": replacement,
    }


def cancel_active(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Отменяет активную задачу.

    Только очищает task lifecycle. Артефакты (incident_report,
    presentation), уже сохранённые в memory/artifacts, не удаляются —
    это ответственность конкретного workflow, а не lifecycle service.
    """
    if state["active_task"] is None:
        return {}

    return {"active_task": None}


def set_awaiting_input(
    state: ConversationState,
    *,
    interaction: Interaction,
    stage: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Переводит active_task в awaiting_input с конкретным Interaction.

    stage должен совпадать с interaction.continuation_stage — это то место,
    куда workflow вернётся после ответа пользователя.
    """
    active = state["active_task"]

    if active is None:
        raise TaskLifecycleError(
            "Cannot set awaiting_input without active_task."
        )

    if interaction.continuation_stage != stage:
        raise TaskLifecycleError(
            "interaction.continuation_stage must match stage: "
            f"{interaction.continuation_stage!r} != {stage!r}"
        )

    now = utc_now_iso()

    updated = active.model_copy(
        update={
            "status": "awaiting_input",
            "pending_interaction": interaction,
            "snapshot": TaskSnapshot(
                stage=stage,
                data={
                    **active.snapshot.data,
                    **(data or {}),
                },
            ),
            "updated_at": now,
        }
    )

    return {"active_task": updated}


def advance_stage(
    state: ConversationState,
    *,
    stage: str,
    data: dict[str, Any] | None = None,
    refs: list[DomainRef] | None = None,
) -> dict[str, Any]:
    """
    Продвигает active_task на новый этап без interaction.

    Используется workflow node-ами между вызовами LLM внутри одного
    workflow (например: normalizer завершился -> executor начинается).
    """
    active = state["active_task"]

    if active is None:
        raise TaskLifecycleError(
            "Cannot advance stage without active_task."
        )

    now = utc_now_iso()

    merged_refs = list(active.refs)

    for ref in refs or []:
        merged_refs = [
            existing
            for existing in merged_refs
            if not (
                existing.kind == ref.kind
                and existing.id == ref.id
            )
        ]
        merged_refs.append(ref)

    updated = active.model_copy(
        update={
            "status": "active",
            "pending_interaction": None,
            "snapshot": TaskSnapshot(
                stage=stage,
                data={
                    **active.snapshot.data,
                    **(data or {}),
                },
            ),
            "refs": merged_refs,
            "updated_at": now,
        }
    )

    return {"active_task": updated}


def resolve_interaction_answer(
    state: ConversationState,
) -> tuple[ConversationTask, Interaction]:
    """
    Достаёт active_task + pending_interaction для узла-владельца ответа.

    Используется тем workflow node-ом, который должен интерпретировать
    ответ пользователя (Search normalizer/executor, RCA gate, Editor,
    Presentation) — сам lifecycle не решает, что означает ответ.
    """
    active = state["active_task"]

    if active is None or active.pending_interaction is None:
        raise TaskLifecycleError(
            "No active_task with pending_interaction."
        )

    return active, active.pending_interaction


def complete_active(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Помечает active_task как completed и снимает её со слота active.

    Завершённая задача не хранится в active/suspended slots: её след
    остаётся только в артефактах (incident_report, presentation) и в
    истории messages, как и требует единый диалог с пользователем.
    """
    if state["active_task"] is None:
        return {}

    return {"active_task": None}


def fail_active(
    state: ConversationState,
) -> dict[str, Any]:
    """
    Прерывает active_task при неустранимой ошибке workflow.

    Сообщение пользователю формирует сам workflow node (понятный текст без
    traceback), lifecycle только освобождает слот.
    """
    if state["active_task"] is None:
        return {}

    return {"active_task": None}