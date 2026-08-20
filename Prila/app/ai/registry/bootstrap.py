from __future__ import annotations


_registered = False


def register_builtin_workflows() -> None:
    """
    Импорт workflow modules выполняет регистрацию factory через декораторы.

    Workflows stateless: все task-specific данные живут только в
    ConversationTask.snapshot.
    """
    global _registered

    if _registered:
        return

    from app.ai.workflows.editor import workflow as _editor_workflow
    from app.ai.workflows.presentation import (
        workflow as _presentation_workflow,
    )
    from app.ai.workflows.rca import workflow as _rca_workflow
    from app.ai.workflows.search import workflow as _search_workflow

    _registered = True