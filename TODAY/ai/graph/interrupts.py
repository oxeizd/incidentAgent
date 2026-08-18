from __future__ import annotations

from typing import Any, Literal


InterruptType = Literal[
    "question",
    "confirmation",
    "form",
]


def ask_user(
    *,
    question: str,
    worker_id: str,
    interaction_type: InterruptType = "question",
    round_number: int,
    options: list[str] | None = None,
    fields: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Единый JSON-serializable payload для LangGraph interrupt().

    `type` — единственный актуальный discriminator для клиента.
    `kind` временно дублируется только ради совместимости со старым UI,
    который ещё мог читать это поле.
    """
    payload: dict[str, Any] = {
        "interaction_id": f"{worker_id}:{round_number}",
        "worker_id": worker_id,
        "question": question,
        "type": interaction_type,
        "kind": interaction_type,
        "round": round_number,
    }

    if options:
        payload["options"] = options

    if fields:
        payload["fields"] = fields

    if metadata:
        payload["metadata"] = metadata

    return payload