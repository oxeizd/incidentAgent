from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class PayloadSchema(BaseModel):
    model_config = ConfigDict(extra="allow")


class SearchPayload(PayloadSchema):
    model_config = ConfigDict(extra="forbid")
    raw_query: str
    search_query: Optional[str] = None
    search_results: list[dict] = Field(default_factory=list)


class RCAPayload(PayloadSchema):
    model_config = ConfigDict(extra="forbid")

    gate_status: Optional[Literal[
        "root_cause_present", "insufficient_info", "contradictory_or_unclear", "no_incident_data",
    ]] = None
    incident_summary: str = ""
    impact: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    mitigation: list[str] = Field(default_factory=list)
    suspected_root_cause: Optional[str] = None
    root_cause_present: bool = False
    root_cause_statement: Optional[str] = None
    causal_chain: list[str] = Field(default_factory=list)
    evidence_found: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_reason: str = ""
    user_answers: dict[str, str] = Field(default_factory=dict)

    analysis: str = ""
    tasks: list[dict] = Field(default_factory=list)

    validated_tasks: list[dict] = Field(default_factory=list)


class EditorPayload(PayloadSchema):
    model_config = ConfigDict(extra="forbid")
    target_artifact_id: str
    target_section: str
    instruction: str
    task_index: Optional[int] = Field(
        None, description="0-based индекс задачи в tasks — если задан, редактируется структурно эта задача, а не текст всей секции.",
    )
    proposed_diff: Optional[dict] = None
    applied_patches: list[dict] = Field(default_factory=list)


class CreatorPayload(PayloadSchema):
    model_config = ConfigDict(extra="forbid")
    source_artifact_id: Optional[str] = None
    collected: dict = Field(default_factory=dict)
    html: str = ""