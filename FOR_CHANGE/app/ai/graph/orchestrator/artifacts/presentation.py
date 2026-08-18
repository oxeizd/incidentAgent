from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState
from app.memory.repository.threads import get_thread_owner
from memory.artifacts.presentations.document import PresentationDocument
from memory.facade import MemoryFacade


class PresentationResultHandler:
    """
    Persist the creator result as one complete presentation document.

    The worker builds data only. It does not generate or persist HTML.
    HTML is rendered on demand from `presentations.fields` by the presentation
    download endpoint.
    """

    def __init__(self, memory: MemoryFacade) -> None:
        self._memory = memory

    async def apply(
        self,
        state: OrchestratorState,
        worker: WorkerState,
    ) -> dict[str, Any]:
        if worker.get("status") != "done":
            return {}

        summary = worker.get("summary_for_parent")
        if not isinstance(summary, dict):
            return {}

        raw_document = summary.get("presentation_document")
        if not isinstance(raw_document, dict):
            return {}

        thread_id = state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError(
                "Orchestrator state must contain a non-empty thread_id"
            )

        owner_user_id = await get_thread_owner(thread_id)
        if not owner_user_id:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Не удалось сохранить презентацию: "
                            "не определён владелец треда."
                        )
                    )
                ]
            }

        document = PresentationDocument.model_validate(raw_document)

        presentation_id = await self._memory.create_presentation(
            user_id=owner_user_id,
            thread_id=thread_id,
            fields=document,
        )

        return {
            "messages": [
                AIMessage(
                    content=(
                        "Презентация готова. "
                        "Черновик сохранён в «Моих презентациях»: "
                        f"{presentation_id}."
                    )
                )
            ]
        }