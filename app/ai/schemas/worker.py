from __future__ import annotations
from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict, NotRequired
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage


class WorkerState(TypedDict):
    worker_id: str
    kind: str
    parent_worker_id: Optional[str]
    history: Annotated[list[AnyMessage], add_messages]
    input_context: dict
    payload: dict
    status: Literal["running", "awaiting_user_input", "done", "failed", "cancelled", "validated", "low_confidence_stop", "deviated"]
    rounds: int
    max_rounds: int
    summary_for_parent: Optional[dict]
    produced_artifact_refs: list[str]
    error: NotRequired[dict]
    reasoning_events: NotRequired[list[dict]]