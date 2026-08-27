from __future__ import annotations

import uuid
from typing import Iterable

from app.ai.schemas.conversation import (
    AgentMessageRole,
    ArtifactRef,
    ConversationTask,
    ExecutionPlan,
    StepRun,
    StepStatus,
    TaskSnapshot,
    UserInputRequest,
    utc_now_iso,
)
from app.ai.schemas.conversation_state import ConversationState


TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {
        "completed",
        "cancelled",
        "failed",
        "skipped",
    }
)


class TaskLifecycleError(Exception):
    """Нарушение инварианта выполнения ExecutionPlan."""


def new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def start_task(
    state: ConversationState,
    *,
    plan: ExecutionPlan,
) -> dict[str, ConversationTask]:
    """Создаёт текущую задачу и runtime-состояние каждого шага DAG."""
    if state["active_task"] is not None:
        raise TaskLifecycleError(
            "Cannot start a task while active_task exists."
        )

    now = utc_now_iso()
    task = ConversationTask(
        task_id=new_task_id(),
        goal=plan.goal,
        status="running",
        snapshot=TaskSnapshot(
            plan=plan,
            step_runs={
                step.step_id: StepRun(step_id=step.step_id)
                for step in plan.steps
            },
        ),
        created_at=now,
        updated_at=now,
    )

    return {"active_task": task}


def suspend_active_task(
    state: ConversationState,
    *,
    reason: str,
) -> dict[str, ConversationTask | None]:
    """Переносит текущую задачу в единственный слот отложенной задачи."""
    active_task = state["active_task"]
    if active_task is None:
        return {}

    now = utc_now_iso()
    suspended_task = active_task.model_copy(
        update={
            "status": "suspended",
            "suspended_at": now,
            "suspension_reason": reason,
            "updated_at": now,
        }
    )

    return {
        "active_task": None,
        "suspended_task": suspended_task,
    }


def resume_suspended_task(
    state: ConversationState,
    *,
    reason: str,
) -> dict[str, ConversationTask | None]:
    """Делает отложенную задачу текущей, при необходимости меняя её местами."""
    suspended_task = state["suspended_task"]
    if suspended_task is None:
        raise TaskLifecycleError("No suspended task to resume.")

    now = utc_now_iso()
    active_task = state["active_task"]
    restored_task = suspended_task.model_copy(
        update={
            "status": "running",
            "suspended_at": None,
            "suspension_reason": None,
            "updated_at": now,
        }
    )

    if active_task is None:
        return {
            "active_task": restored_task,
            "suspended_task": None,
        }

    replacement_task = active_task.model_copy(
        update={
            "status": "suspended",
            "suspended_at": now,
            "suspension_reason": reason,
            "updated_at": now,
        }
    )

    return {
        "active_task": restored_task,
        "suspended_task": replacement_task,
    }


def cancel_active_task(
    state: ConversationState,
) -> dict[str, None]:
    """
    Убирает текущую задачу из state.

    Внешний background runtime отдельно должен получить cancellation signal.
    Уже сохранённые domain artifacts не удаляются.
    """
    if state["active_task"] is None:
        return {}

    return {"active_task": None}


def get_active_task(state: ConversationState) -> ConversationTask:
    active_task = state["active_task"]
    if active_task is None:
        raise TaskLifecycleError("No active task.")

    return active_task


def get_plan_step(
    task: ConversationTask,
    *,
    step_id: str,
):
    for step in task.snapshot.plan.steps:
        if step.step_id == step_id:
            return step

    raise TaskLifecycleError(f"Unknown step: {step_id!r}.")


def get_step_run(
    task: ConversationTask,
    *,
    step_id: str,
) -> StepRun:
    step_run = task.snapshot.step_runs.get(step_id)
    if step_run is None:
        raise TaskLifecycleError(f"Missing StepRun for step: {step_id!r}.")

    return step_run


def ready_step_ids(task: ConversationTask) -> list[str]:
    """
    Возвращает pending шаги, готовые к одному новому запуску worker-а.

    После ответа пользователя receive_user_input() возвращает ожидавший шаг в
    pending, потому что прошлый worker invocation уже завершился. Scheduler
    увидит его как обычный готовый шаг и вызовет тот же worker повторно.
    """
    ready: list[str] = []

    for step in task.snapshot.plan.steps:
        step_run = get_step_run(task, step_id=step.step_id)
        if step_run.status != "pending":
            continue

        dependency_runs = [
            get_step_run(task, step_id=dependency_id)
            for dependency_id in step.depends_on
        ]

        if all(run.status == "completed" for run in dependency_runs):
            ready.append(step.step_id)

    return ready


def skip_blocked_steps(
    state: ConversationState,
) -> dict[str, ConversationTask] | dict[object, object]:
    """Каскадно пропускает pending downstream после failed/cancelled шага."""
    task = get_active_task(state)
    step_runs = dict(task.snapshot.step_runs)
    changed = False

    while True:
        pass_changed = False

        for step in task.snapshot.plan.steps:
            step_run = step_runs[step.step_id]
            if step_run.status != "pending":
                continue

            dependency_statuses = [
                step_runs[dependency_id].status
                for dependency_id in step.depends_on
            ]
            if not any(
                status in {"cancelled", "failed", "skipped"}
                for status in dependency_statuses
            ):
                continue

            step_runs[step.step_id] = step_run.model_copy(
                update={
                    "status": "skipped",
                    "completed_at": utc_now_iso(),
                }
            )
            changed = True
            pass_changed = True

        if not pass_changed:
            break

    if not changed:
        return {}

    return _replace_active_task(
        task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"step_runs": step_runs}
                )
            }
        )
    )


def mark_step_running(
    state: ConversationState,
    *,
    step_id: str,
) -> dict[str, ConversationTask]:
    step_run = get_step_run(get_active_task(state), step_id=step_id)
    update: dict[str, object] = {
        "status": "running",
        "error": None,
    }

    if step_run.started_at is None:
        update["started_at"] = utc_now_iso()

    return _replace_step_run(state, step_id=step_id, update=update)


def mark_step_completed(
    state: ConversationState,
    *,
    step_id: str,
    output_refs: Iterable[ArtifactRef] = (),
) -> dict[str, ConversationTask]:
    return _replace_step_run(
        state,
        step_id=step_id,
        update={
            "status": "completed",
            "output_refs": list(output_refs),
            "error": None,
            "completed_at": utc_now_iso(),
        },
    )


def mark_step_failed(
    state: ConversationState,
    *,
    step_id: str,
    error: str,
) -> dict[str, ConversationTask]:
    return _replace_step_run(
        state,
        step_id=step_id,
        update={
            "status": "failed",
            "error": error,
            "completed_at": utc_now_iso(),
        },
    )


def cancel_step_and_downstream(
    state: ConversationState,
    *,
    step_id: str,
) -> dict[str, ConversationTask]:
    """Отменяет выбранный шаг и весь зависящий от него downstream."""
    task = get_active_task(state)
    get_plan_step(task, step_id=step_id)

    affected_step_ids = _downstream_step_ids(task, root_step_id=step_id)
    step_runs = dict(task.snapshot.step_runs)
    now = utc_now_iso()

    for affected_step_id in affected_step_ids:
        step_run = step_runs[affected_step_id]
        if step_run.status in TERMINAL_STEP_STATUSES:
            continue

        step_runs[affected_step_id] = step_run.model_copy(
            update={
                "status": "cancelled",
                "completed_at": now,
            }
        )

    return _replace_active_task(
        task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"step_runs": step_runs}
                )
            }
        )
    )


def wait_for_user_input(
    state: ConversationState,
    *,
    request: UserInputRequest,
) -> dict[str, ConversationTask]:
    """Останавливает один StepRun и всю задачу до следующего user turn."""
    task = get_active_task(state)
    get_plan_step(task, step_id=request.step_id)

    step_run = get_step_run(task, step_id=request.step_id)
    if step_run.status != "running":
        raise TaskLifecycleError(
            "Only running step may wait for user input."
        )

    updated_step_runs = dict(task.snapshot.step_runs)
    updated_step_runs[request.step_id] = step_run.model_copy(
        update={"status": "waiting_for_user"}
    )

    updated_task = task.model_copy(
        update={
            "status": "waiting_for_user",
            "pending_user_input": request,
            "snapshot": task.snapshot.model_copy(
                update={"step_runs": updated_step_runs}
            ),
            "updated_at": utc_now_iso(),
        }
    )

    return {"active_task": updated_task}


def receive_user_input(
    state: ConversationState,
    *,
    user_text: str,
) -> dict[str, ConversationTask]:
    """
    Добавляет ответ пользователя в историю ожидающего шага и ставит его pending.

    `pending`, а не `running`: invocation worker-а, который задал вопрос, уже
    закончился. Следующий scheduler pass заново вызовет worker с сохранённой
    user/assistant перепиской.
    """
    task = get_active_task(state)
    request = task.pending_user_input
    if request is None:
        raise TaskLifecycleError("Active task has no pending user input.")

    step_run = get_step_run(task, step_id=request.step_id)
    if step_run.status != "waiting_for_user":
        raise TaskLifecycleError(
            "Pending user input belongs to a step that is not waiting."
        )

    updated_step_runs = dict(task.snapshot.step_runs)
    updated_step_runs[request.step_id] = step_run.model_copy(
        update={
            "status": "pending",
            "conversation": step_run.conversation.append(
                role="user",
                content=user_text,
            ),
        }
    )

    updated_task = task.model_copy(
        update={
            "status": "running",
            "pending_user_input": None,
            "snapshot": task.snapshot.model_copy(
                update={"step_runs": updated_step_runs}
            ),
            "updated_at": utc_now_iso(),
        }
    )

    return {"active_task": updated_task}


def append_step_message(
    state: ConversationState,
    *,
    step_id: str,
    role: AgentMessageRole,
    content: str,
) -> dict[str, ConversationTask]:
    """Добавляет обычное user/assistant сообщение в историю одного StepRun."""
    task = get_active_task(state)
    step_run = get_step_run(task, step_id=step_id)

    return _replace_step_run(
        state,
        step_id=step_id,
        update={
            "conversation": step_run.conversation.append(
                role=role,
                content=content,
            )
        },
    )


def is_task_terminal(task: ConversationTask) -> bool:
    """Возвращает True, когда каждый шаг DAG имеет terminal status."""
    return all(
        step_run.status in TERMINAL_STEP_STATUSES
        for step_run in task.snapshot.step_runs.values()
    )


def finalize_active_task(
    state: ConversationState,
) -> dict[str, None]:
    """Освобождает active_task после terminal завершения всех шагов DAG."""
    task = get_active_task(state)
    if not is_task_terminal(task):
        raise TaskLifecycleError("Cannot finalize a non-terminal task.")

    return {"active_task": None}


def _replace_step_run(
    state: ConversationState,
    *,
    step_id: str,
    update: dict[str, object],
) -> dict[str, ConversationTask]:
    task = get_active_task(state)
    step_run = get_step_run(task, step_id=step_id)
    step_runs = dict(task.snapshot.step_runs)
    step_runs[step_id] = step_run.model_copy(update=update)

    return _replace_active_task(
        task.model_copy(
            update={
                "snapshot": task.snapshot.model_copy(
                    update={"step_runs": step_runs}
                )
            }
        )
    )


def _replace_active_task(
    task: ConversationTask,
) -> dict[str, ConversationTask]:
    return {
        "active_task": task.model_copy(
            update={"updated_at": utc_now_iso()}
        )
    }


def _downstream_step_ids(
    task: ConversationTask,
    *,
    root_step_id: str,
) -> set[str]:
    children_by_step: dict[str, set[str]] = {
        step.step_id: set()
        for step in task.snapshot.plan.steps
    }

    for step in task.snapshot.plan.steps:
        for dependency_id in step.depends_on:
            children_by_step[dependency_id].add(step.step_id)

    affected = {root_step_id}
    pending = [root_step_id]

    while pending:
        current_step_id = pending.pop()
        for child_step_id in children_by_step[current_step_id]:
            if child_step_id in affected:
                continue

            affected.add(child_step_id)
            pending.append(child_step_id)

    return affected