from __future__ import annotations
from app.ai.schemas.payloads import PayloadSchema

PAYLOAD_SCHEMAS: dict[str, type[PayloadSchema]] = {}


def register_payload_schema(kind: str, schema_cls: type[PayloadSchema]) -> None:
    """
    Прямая регистрация payload-схемы БЕЗ workflow — только для служебных/
    тестовых kind, не участвующих в WORKFLOW_REGISTRY.

    Для обычных workflow схема регистрируется автоматически через
    app.ai.registry.workflows.register_workflow(payload_schema=...) — не
    вызывайте оба способа для одного и того же kind, иначе ValueError.
    """
    if kind in PAYLOAD_SCHEMAS:
        raise ValueError(f"payload schema for '{kind}' already registered")
    PAYLOAD_SCHEMAS[kind] = schema_cls


def validate_payload(kind: str, data: dict) -> dict:
    schema = PAYLOAD_SCHEMAS.get(kind)
    if not schema:
        raise ValueError(f"Unknown workflow kind (no payload schema): {kind}")
    return schema(**data).model_dump()
