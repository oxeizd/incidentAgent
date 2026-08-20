from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.runtime.services import get_memory
from app.ai.workflows.search.contracts import SearchPlan


@dataclass(frozen=True, slots=True)
class SearchExecution:
    """
    Результат выполнения нормализованного SearchPlan.

    artifact — существующий search result memory layer. Его рендеринг
    остаётся в app.memory.search.markdown, чтобы UI/Markdown-preview
    полностью совпадали с текущим поведением.
    """

    artifact: Any

    @property
    def result_id(self) -> str:
        return self.artifact.result_id

    @property
    def entity(self) -> str:
        return self.artifact.entity

    @property
    def total_count(self) -> int:
        return self.artifact.total_count

    @property
    def preview_count(self) -> int:
        return self.artifact.preview_count


async def execute_search(
    *,
    plan: SearchPlan,
    user_id: str,
    thread_id: str,
    preview_limit: int = 5,
) -> SearchExecution:
    """
    Выполняет готовый SearchPlan через текущий MemoryFacade.

    Формат и persistence search result не меняются.
    """
    memory = get_memory()

    if plan.mode == "structured":
        if plan.entity == "incidents":
            artifact = await memory.search_incidents(
                user_id=user_id,
                thread_id=thread_id,
                filters=plan.filters or {},
                preview_limit=preview_limit,
            )
        else:
            artifact = await memory.search_assignments(
                user_id=user_id,
                thread_id=thread_id,
                filters=plan.filters or {},
                preview_limit=preview_limit,
            )
    elif plan.entity == "incidents":
        artifact = await memory.find_similar_incidents(
            user_id=user_id,
            thread_id=thread_id,
            query_text=plan.query_text or "",
            limit=preview_limit,
            preview_limit=preview_limit,
        )
    else:
        artifact = await memory.find_similar_assignments(
            user_id=user_id,
            thread_id=thread_id,
            query_text=plan.query_text or "",
            limit=preview_limit,
            preview_limit=preview_limit,
        )

    return SearchExecution(artifact=artifact)