"""app/ai/graph/orchestrator/models.py — структурированные схемы для LLM-вызовов оркестратора."""
from __future__ import annotations
from typing import Literal, Optional

from pydantic import BaseModel, Field


class IntentClassification(BaseModel):
    intent: Literal[
        "new_search", "search_then_analyze", "analyze",
        "resume_previous", "edit_report", "reanalyze_report",
        "create_presentation",
        "cancel_current", "chitchat_or_other",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    incident_number: Optional[str] = Field(
        None, description="Номер инцидента (при intent=analyze, или create_presentation без готового отчёта).",
    )
    raw_description: Optional[str] = Field(
        None, description="Свободное описание проблемы (при intent=analyze, или create_presentation без готового отчёта).",
    )
    resolved_query: Optional[str] = Field(
        None,
        description=(
            "только для new_search/search_then_analyze: самодостаточный перефраз "
            "последней реплики пользователя с учётом истории диалога."
        ),
    )
    evidence: Optional[str] = Field(
        None,
        description=(
            "только для reanalyze_report: точная формулировка нового факта/лога/"
            "метрики/результата отката, который пользователь только сообщил."
        ),
    )


class EditRequest(BaseModel):
    target_section: str
    instruction: str
    task_number: Optional[int] = Field(
        None,
        description=(
            "Номер меры КАК НАЗВАЛ ПОЛЬЗОВАТЕЛЬ (1-based, ровно как в показанном "
            "ему списке) — только если target_section='tasks'. НЕ вычисляй "
            "0-based индекс сам, просто верни то же число, что в реплике."
        ),
    )


class RoutingDecision(BaseModel):
    """
    Wave 4: typed output IntentClassifier.classify() — развязывает
    classifier от LLM structured-output схемы IntentClassification, чтобы
    внутреннюю реализацию можно было менять, не трогая state-update ключи,
    которые уже могли быть записаны в существующие checkpoints.
    """
    intent: str
    confidence: float
    incident_number: Optional[str] = None
    raw_description: Optional[str] = None
    resolved_query: Optional[str] = None
    evidence: Optional[str] = None

    def to_state_update(self) -> dict:
        """
        Ключи ДОЛЖНЫ совпадать с тем, что писал старый classify_intent() в
        app/ai/graph/orchestrator.py — иначе существующие checkpoints,
        записанные до миграции, не смогут корректно resume'иться.
        """
        return {
            "intent": self.intent, "intent_confidence": self.confidence,
            "_incident_number": self.incident_number, "_raw_description": self.raw_description,
            "_resolved_query": self.resolved_query, "_evidence": self.evidence,
        }

    @classmethod
    def from_classification(cls, result: IntentClassification) -> "RoutingDecision":
        return cls(
            intent=result.intent, confidence=result.confidence,
            incident_number=result.incident_number, raw_description=result.raw_description,
            resolved_query=result.resolved_query, evidence=result.evidence,
        )
