from __future__ import annotations

from app.ai.workflows.dispatcher import register_step_worker

_registered = False


def register_builtin_workflows() -> None:
    """
    Регистрирует три исполнителя шагов ExecutionPlan.

    Вызывается один раз при старте приложения до построения conversation graph.
    Редактирование не является отдельным workflow: RCA и presentation сами
    умеют обновлять свои доменные документы в рамках собственного шага.
    """
    global _registered

    if _registered:
        return

    from app.ai.workflows.presentation import execute_presentation_step
    from app.ai.workflows.rca import execute_rca_step
    from app.ai.workflows.search import execute_search_step

    register_step_worker(
        kind="search",
        worker=execute_search_step,
    )
    register_step_worker(
        kind="rca",
        worker=execute_rca_step,
    )
    register_step_worker(
        kind="presentation",
        worker=execute_presentation_step,
    )

    _registered = True