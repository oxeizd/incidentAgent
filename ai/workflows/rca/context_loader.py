from __future__ import annotations

from typing import Any

from app.ai.runtime.services import get_memory
from app.ai.schemas.conversation import (
    ArtifactRef,
    IncidentRef,
    IncidentReportRef,
    PlanStep,
)
from app.ai.schemas.conversation_state import ConversationState
from app.ai.workflows.rca.contracts import RCAInput


class RCAContextLoadError(RuntimeError):
    """Не удалось безопасно загрузить исходные данные для RCA."""


async def load_rca_input(
    *,
    state: ConversationState,
    step: PlanStep,
    upstream_refs: list[ArtifactRef],
) -> RCAInput:
    """
    Загружает полный runtime payload для RCA.

    Приоритет источников:
    1. IncidentReportRef: пользователь редактирует/продолжает RCA-справку.
    2. IncidentRef: анализ конкретного инцидента из search upstream.
    3. incident_number в inputs: прямой запрос без отдельного search step.
    4. raw_description в inputs: RCA по свободному описанию.

    Payload живёт только в памяти этого запуска worker-а. В TaskSnapshot
    сохраняются лишь ArtifactRef и user/assistant история шага.
    """
    report_ref = _find_ref(upstream_refs, kind="incident_report")
    if report_ref is not None:
        return await _load_report_input(
            state=state,
            report_ref=IncidentReportRef.model_validate(
                report_ref.model_dump(mode="json")
            ),
        )

    input_ref = _parse_input_ref(step.inputs.get("source_ref"))
    if input_ref is not None and input_ref.kind == "incident_report":
        return await _load_report_input(
            state=state,
            report_ref=IncidentReportRef.model_validate(
                input_ref.model_dump(mode="json")
            ),
        )

    incident_ref = _find_ref(upstream_refs, kind="incident")
    if incident_ref is not None:
        return await _load_incident_input(
            IncidentRef.model_validate(incident_ref.model_dump(mode="json"))
        )

    if input_ref is not None and input_ref.kind == "incident":
        return await _load_incident_input(
            IncidentRef.model_validate(input_ref.model_dump(mode="json"))
        )

    incident_number = step.inputs.get("incident_number")
    if isinstance(incident_number, str) and incident_number.strip():
        number = incident_number.strip()
        return await _load_incident_input(
            IncidentRef(
                id=number,
                number=number,
                label=number,
            )
        )

    raw_description = step.inputs.get("raw_description")
    if isinstance(raw_description, str) and raw_description.strip():
        description = raw_description.strip()
        return RCAInput(
            kind="description",
            raw_description=description,
            source_payload={"description": description},
        )

    raise RCAContextLoadError(
        "Для RCA нужен инцидент, RCA-справка или описание проблемы."
    )


async def _load_incident_input(
    incident_ref: IncidentRef,
) -> RCAInput:
    number = (incident_ref.number or "").strip()
    if not number:
        raise RCAContextLoadError("Для RCA не указан номер инцидента.")

    incident = await get_memory().get_incident(number=number)
    if incident is None:
        raise RCAContextLoadError(f"Инцидент {number} не найден.")

    return RCAInput(
        kind="incident",
        incident_number=number,
        source_payload={"incident": incident},
    )


async def _load_report_input(
    *,
    state: ConversationState,
    report_ref: IncidentReportRef,
) -> RCAInput:
    report = await get_memory().get_incident_report_for_agent(
        user_id=state["user_id"],
        thread_id=state["thread_id"],
        report_id=report_ref.id,
    )

    return RCAInput(
        kind="report",
        report_id=report_ref.id,
        source_payload={
            "report": {
                "id": report.id,
                "current_version": report.current_version,
                "status": report.status,
                "sections": report.sections,
            }
        },
    )


def collect_upstream_refs(
    *,
    state: ConversationState,
    step: PlanStep,
) -> list[ArtifactRef]:
    """Собирает output refs прямых dependencies текущего RCA шага."""
    task = state["active_task"]
    if task is None:
        raise RCAContextLoadError("Нет активной задачи RCA.")

    return [
        ref
        for dependency_id in step.depends_on
        for ref in task.snapshot.step_runs[dependency_id].output_refs
    ]


def _find_ref(
    refs: list[ArtifactRef],
    *,
    kind: str,
) -> ArtifactRef | None:
    matches = [ref for ref in refs if ref.kind == kind]

    if len(matches) > 1:
        raise RCAContextLoadError(
            f"RCA получил несколько источников типа {kind!r}."
        )

    return matches[0] if matches else None


def _parse_input_ref(value: Any) -> ArtifactRef | None:
    if value is None:
        return None

    if isinstance(value, ArtifactRef):
        return value

    if isinstance(value, dict):
        return ArtifactRef.model_validate(value)

    raise RCAContextLoadError("source_ref RCA имеет неверный формат.")