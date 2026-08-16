"""
app/ai/graph/orchestrator/artifacts/incident_report.py

Wave 6: владеет lifecycle incident_report-артефакта. Раньше эта логика
(_create_report_artifact_update + _gate_result_from_payload +
_format_gate_result_block) жила прямо в app/ai/graph/orchestrator.py и была
доступна через словарь _ARTIFACT_HOOKS["rca"] — единственная строчка
диспетчеризации по kind в этом файле теперь тоже убрана: handlers обращаются
к app/ai/graph/orchestrator/artifacts/registry.py:ArtifactHandlerRegistry, не
к kind-специфичным if'ам.

gate_result_from_payload / format_gate_result_block вынесены как публичные
функции — их использует и этот handler (создание нового артефакта), и
handlers/reanalyze.py (обновление существующего артефакта новыми фактами),
и handlers/edit.py (форматирование rca-контекста для editor-воркера при
замене системной меры).
"""
from __future__ import annotations
from typing import Any

from app.ai.runtime.artifacts import create_artifact
from app.ai.schemas.orchestrator import OrchestratorState
from app.ai.schemas.worker import WorkerState


def gate_result_from_payload(payload: dict) -> dict:
    return {
        "root_cause_statement": payload.get("root_cause_statement"),
        "causal_chain": payload.get("causal_chain", []),
        "impact": payload.get("impact", []),
        "symptoms": payload.get("symptoms", []),
        "mitigation": payload.get("mitigation", []),
        "incident_summary": payload.get("incident_summary", ""),
    }


def format_gate_result_block(gate_result: dict) -> str:
    lines = [
        f"Корневая причина: {gate_result.get('root_cause_statement')}" if gate_result.get("root_cause_statement") else "",
        f"Цепочка 5 почему: {'; '.join(gate_result.get('causal_chain', []))}" if gate_result.get("causal_chain") else "",
        f"Симптомы: {'; '.join(gate_result.get('symptoms', []))}" if gate_result.get("symptoms") else "",
        f"Влияние: {'; '.join(gate_result.get('impact', []))}" if gate_result.get("impact") else "",
    ]
    return "\n".join(line for line in lines if line)


class IncidentReportResultHandler:
    """
    Создаёт incident_report-артефакт только на первый успешный прогон
    RCA-воркера (summary с analysis+tasks). Повторный анализ
    (reanalyze_report) версионирует УЖЕ существующий артефакт напрямую в
    handlers/reanalyze.py через replace_artifact_sections — тот путь
    заменяет секции конкретного artifact_id, а не создаёт новый, поэтому
    сюда не заходит.
    """

    async def apply(self, state: OrchestratorState, worker: WorkerState) -> dict[str, Any]:
        if worker["status"] != "done":
            return {}
        summary = worker.get("summary_for_parent") or {}
        if "analysis" not in summary or "tasks" not in summary:
            return {}

        payload = worker.get("payload") or {}
        input_context = worker.get("input_context") or {}
        rca_input = {
            "incident_number": input_context.get("incident_number"),
            "raw_description": input_context.get("raw_description"),
            "search_summary": input_context.get("search_summary"),
            "evidence": input_context.get("evidence", []),
            "gate_result": gate_result_from_payload(payload),
        }

        artifact_id = f"incident_report-{worker['worker_id']}"
        artifact = create_artifact(
            artifact_id, "incident_report",
            {"analysis": summary["analysis"], "tasks": summary["tasks"], "_rca_input": rca_input},
            created_by_worker_id=worker["worker_id"],
        )
        return {
            "artifacts": {**state["artifacts"], artifact_id: artifact},
            "current_artifact_id": artifact_id,
        }


handler = IncidentReportResultHandler()
