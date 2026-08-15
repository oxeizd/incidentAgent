from __future__ import annotations
from typing import TypeVar, Generic
from app.ai.schemas.worker import WorkerState
from app.ai.schemas.payloads import PayloadSchema
from app.ai.registry.payloads import PAYLOAD_SCHEMAS

PayloadSchemaT = TypeVar("PayloadSchemaT", bound=PayloadSchema)


class TypedWorkerState(Generic[PayloadSchemaT]):
    def __init__(self, raw_state: WorkerState, schema_cls: type[PayloadSchemaT]):
        self._raw = raw_state
        self._schema = schema_cls
        self._validated_payload: PayloadSchemaT | None = None

    @property
    def payload(self) -> PayloadSchemaT:
        if self._validated_payload is None:
            self._validated_payload = self._schema(**self._raw["payload"])
        return self._validated_payload

    @payload.setter
    def payload(self, value) -> None:
        validated = self._schema(**value) if isinstance(value, dict) else value
        self._validated_payload = validated
        self._raw["payload"] = validated.model_dump()

    def __getattr__(self, name: str):
        if name not in self._raw:
            raise AttributeError(f"{name!r} is not a field of WorkerState (worker_id={self._raw.get('worker_id')!r})")
        return self._raw[name]


def get_typed(worker: WorkerState) -> TypedWorkerState:
    schema_cls = PAYLOAD_SCHEMAS.get(worker["kind"])
    if schema_cls is None:
        raise ValueError(f"No payload schema registered for kind={worker['kind']!r}")
    return TypedWorkerState(worker, schema_cls)