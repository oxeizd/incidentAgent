from __future__ import annotations

from typing import Generic, TypeVar

from app.ai.registry.payloads import PAYLOAD_SCHEMAS
from app.ai.schemas.payloads import PayloadSchema
from app.ai.schemas.worker import WorkerState


PayloadT = TypeVar("PayloadT", bound=PayloadSchema)


class TypedWorkerState(Generic[PayloadT]):
    """
    Typed facade поверх raw WorkerState.

    `worker["payload"]` остаётся обычным serializable dict для LangGraph.
    Ноды работают с `ctx.typed.payload` как с Pydantic model.
    """

    def __init__(
        self,
        raw_worker: WorkerState,
        payload_schema: type[PayloadT],
    ) -> None:
        self._worker = raw_worker
        self._payload_schema = payload_schema
        self._payload: PayloadT | None = None

    @property
    def payload(self) -> PayloadT:
        if self._payload is None:
            self._payload = self._payload_schema.model_validate(
                self._worker["payload"]
            )

        return self._payload

    @payload.setter
    def payload(self, value: PayloadT | dict) -> None:
        parsed = (
            self._payload_schema.model_validate(value)
            if isinstance(value, dict)
            else value
        )

        self._payload = parsed
        self._worker["payload"] = parsed.model_dump()

    @property
    def worker(self) -> WorkerState:
        return self._worker


def get_typed(worker: WorkerState) -> TypedWorkerState:
    schema = PAYLOAD_SCHEMAS.get(worker["kind"])

    if schema is None:
        raise ValueError(
            f"No payload schema registered for worker kind "
            f"{worker['kind']!r}"
        )

    return TypedWorkerState(worker, schema)