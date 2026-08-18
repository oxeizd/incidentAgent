from __future__ import annotations

import types
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel


_EMPTY_VALUES = (
    None,
    "",
    "—",
    [],
    {},
    (),
)


def build_fields_schema(
    schema: type[BaseModel],
    *,
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Строит declarative форму для interrupt(type='form') из Pydantic schema.

    required_for_completion и label определяются metadata в Field:
      Field(..., json_schema_extra={
          "required_for_completion": True,
          "label": "номер инцидента",
      })

    Не создаёт второй hardcoded список required fields.
    """
    current_values = current or {}
    fields: list[dict[str, Any]] = []

    for name, field_info in schema.model_fields.items():
        extra = (
            field_info.json_schema_extra
            if isinstance(field_info.json_schema_extra, dict)
            else {}
        )

        raw_value = current_values.get(name)
        field_type = _json_type_for(field_info.annotation)

        item: dict[str, Any] = {
            "name": name,
            "label": str(extra.get("label", name)),
            "required": bool(
                extra.get("required_for_completion")
            ),
            "type": field_type,
            "value": (
                None
                if raw_value in _EMPTY_VALUES
                else raw_value
            ),
        }

        if field_type == "array":
            item["items"] = {
                "type": _array_item_type(field_info.annotation)
            }

        fields.append(item)

    return fields


def _json_type_for(annotation: Any) -> str:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation) or annotation

    if origin is bool:
        return "boolean"

    if origin is int:
        return "integer"

    if origin is float:
        return "number"

    if origin in (list, tuple, set):
        return "array"

    if origin is dict:
        return "object"

    return "string"


def _array_item_type(annotation: Any) -> str:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin not in (list, tuple, set):
        return "string"

    arguments = get_args(annotation)

    if not arguments:
        return "string"

    return _json_type_for(arguments[0])


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)

    if origin in (typing.Union, types.UnionType):
        values = [
            value
            for value in get_args(annotation)
            if value is not type(None)
        ]

        if len(values) == 1:
            return values[0]

    return annotation