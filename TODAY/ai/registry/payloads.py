from __future__ import annotations

from app.ai.schemas.payloads import PayloadSchema


PAYLOAD_SCHEMAS: dict[str, type[PayloadSchema]] = {}


def register_payload_schema(
    kind: str,
    schema_cls: type[PayloadSchema],
) -> None:
    """
    Регистрирует payload schema для служебного/test workflow.

    Для обычных worker-ов schema регистрируется автоматически внутри
    register_workflow(). Не нужно вызывать оба пути для одного kind.
    """
    if kind in PAYLOAD_SCHEMAS:
        raise ValueError(
            f"Payload schema for workflow {kind!r} is already registered"
        )

    PAYLOAD_SCHEMAS[kind] = schema_cls


def validate_payload(
    kind: str,
    raw_payload: dict,
) -> dict:
    """
    Валидирует и нормализует payload до запуска worker-а.
    """
    schema_cls = PAYLOAD_SCHEMAS.get(kind)

    if schema_cls is None:
        raise ValueError(
            f"Unknown workflow kind {kind!r}: payload schema is not registered"
        )

    return schema_cls.model_validate(raw_payload).model_dump()