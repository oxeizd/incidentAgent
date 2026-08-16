"""
app/ai/graph/orchestrator/artifacts/presentation.py

Wave 6: владеет ПОЛНЫМ lifecycle presentation-артефакта — создание
artifact-записи, сохранение HTML на диск (app/ai/runtime/presentation_storage.py),
запись в "мои презентации" (app/memory/repository/presentations.py) и
пользовательское сообщение со ссылкой на скачивание.

ИЗМЕНЕНИЕ ПОВЕДЕНИЯ (сознательное, часть цели Wave 6): раньше вся эта логика
была инлайном ТОЛЬКО внутри _on_create_presentation в старом orchestrator.py.
Если creator-воркер завершался через resume_previous (после deviation), эта
логика НЕ повторно срабатывала — presentation-артефакт на резюме не создавался,
хотя воркер реально дошёл до status=done с html. Теперь этот handler
зарегистрирован в ArtifactHandlerRegistry под kind="creator" и применяется
УНИФИЦИРОВАННО везде, где creator-воркер завершается (create_presentation
intent И resume_previous) — то самое "no if kind==... behavior" из
Definition of Done. Если это нежелательно — legacy-поведение легко вернуть,
не регистрируя handler для "creator" в handlers/resume.py.
"""
from __future__ import annotations
from typing import Any

from langchain_core.messages import AIMessage

from app.ai.runtime.artifacts import create_artifact
from app.ai.runtime.presentation_storage import save_presentation_html
from app.memory.repository.threads import get_thread_owner
from app.memory.repository.presentations import create_presentation
from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState


class PresentationResultHandler:
    async def apply(self, state: OrchestratorState, worker: WorkerState) -> dict[str, Any]:
        if worker["status"] != "done":
            return {}
        summary = worker.get("summary_for_parent") or {}
        if "html" not in summary:
            return {}

        presentation_id = f"presentation-{worker['worker_id']}"
        presentation_artifact = create_artifact(
            presentation_id, "presentation", {"html": summary["html"]},
            created_by_worker_id=worker["worker_id"],
        )
        artifacts = {**state["artifacts"], presentation_id: presentation_artifact}

        local_path = save_presentation_html(state["thread_id"], presentation_id, summary["html"])

        owner_user_id = await get_thread_owner(state["thread_id"])
        db_presentation_id = None
        if owner_user_id:
            collected = (worker.get("payload") or {}).get("collected") or {}
            analysis_markdown = None
            current_artifact_id = state.get("current_artifact_id")
            if current_artifact_id and current_artifact_id in state["artifacts"]:
                a = state["artifacts"][current_artifact_id]
                analysis_markdown = a["versions"][a["current_version"]]["sections"].get("analysis")
            db_presentation_id = await create_presentation(
                owner_user_id, state["thread_id"], fields=collected, analysis_markdown=analysis_markdown,
            )

        download_url = f"/api/v1/threads/{state['thread_id']}/artifacts/{presentation_id}/file"
        mine_note = (
            f" сохранена в «Моих презентациях» (id: {db_presentation_id}) — можно править поля "
            f"и опубликовать в общее хранилище без нового запроса ко мне."
            if db_presentation_id else ""
        )

        return {
            "artifacts": artifacts,
            "current_artifact_id": presentation_id,
            "messages": [AIMessage(content=(
                f"Презентация готова (см. панель артефакта). "
                f"[Скачать файл]({download_url}).{mine_note} "
                f"также сохранена локально: {local_path}"
            ))],
        }


handler = PresentationResultHandler()
