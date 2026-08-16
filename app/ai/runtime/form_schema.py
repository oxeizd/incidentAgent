"""
app/ai/runtime/form_schema.py

Строит JSON-описание полей формы для interrupt(type="form") из Pydantic-
схемы payload'а (например ExtractedIncidentData в app/ai/nodes/creator.py).

Правило "обязательное/опциональное" — то же, что рекомендует OpenAI для
structured outputs: поле либо обязательно и должно быть заполнено, либо опционально
и остается пустым (null), если данных нет — а не просто "выкидывается" из
контракта. Обязательность помечается в самой схеме через
Field(..., json_schema_extra={"required_for_completion": True, "label": ...}),
см. ExtractedIncidentData.required_for_completion() в creator.py — здесь мы
читаем ту же метадату, чтобы не завести второй параллельный список полей.

ИСПРАВЛЕНО (в этом файле было два допущения):
  1. "type" каждого поля формы раньше был жёстко закодирован как "string"
     для ВСЕХ полей. теперь тип берётся из pydantic-аннотации (bool ->
     boolean, int -> integer, float -> number, list -> array, dict ->
     object, Optional[...] разворачивается до непустого варианта, остальное
     -> string) — то же соответствие типов, что использует JSON Schema.
  2. Для полей с type="array" (например ExtractedIncidentData.timeline —
     список строк хронологии, см. creator.py) не указывался тип ФЛЕМЕНТОВ
     массива ("items") — по JSON Schema массив без "items" неполон.
     теперь для array-полей дополнительно кладётся "items": {"type": ...},
     выведенный из типового параметра (list[str] -> items.type="string").
  3. "Пустое" значение для не-строковых полей (например [] для списка)
     раньше не считалось "данных нет" — value=[] трактовалось как
     заполненное. теперь пустой контейнер тоже даёт value=None, как "—"/""
     для строк.
"""
from __future__ import annotations
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel

_PYTHON_TO_JSON_TYPE: dict[Any, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}

_EMPTY_VALUES = (None, "", "—", [], {}, ())


def _unwrap_optional(annotation: Any) -> Any:
    """Optional[X] (он же Union[X, None]) -> X. Прочие Union — без изменений."""
    origin = get_origin(annotation)
    if origin is typing.Union:
        non_none_args = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _json_type_for(annotation: Any) -> str:
    """
    JSON Schema type ("string"/"boolean"/"integer"/"number"/"array"/"object")
    для pydantic-аннотации поля. Неизвестные/сложные аннотации (кастомные
    модели, Any, нераспознанные generic'и) падают в "string" — консервативный
    дефолт для реально неоднозначных случаев, а не по умолчанию для всех.
    """
    annotation = _unwrap_optional(annotation)
    key = get_origin(annotation) or annotation
    return _PYTHON_TO_JSON_TYPE.get(key, "string")


def _items_type_for(annotation: Any) -> str | None:
    """Для list[T]/tuple[T, ...]/set[T] — JSON type элемента T. Иначе None."""
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin in (list, tuple, set):
        args = get_args(annotation)
        if args:
            return _json_type_for(args[0])
        return "string"
    return None


def build_fields_schema(schema: type[BaseModel], *, current: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Возвращает список полей формы в порядке обьявления в схеме:
      {"name": ..., "label": ..., "required": bool, "type": "string"|"boolean"|
       "integer"|"number"|"array"|"object", "value": ...|None,
       "items": {"type": ...}}  # только когда type == "array"

    value=None означает "данных нет, поле в форме должно быть пустым для
    заполнения"; заполненное значение — "уже известно, показать как
    предзаболненный input" — игр то разделение, которое нужно для
    контекстных опциональных полей презентации.
    """
    fields: list[dict[str, Any]] = []
    for name, field_info in schema.model_fields.items():
        extra = field_info.json_schema_extra if isinstance(field_info.json_schema_extra, dict) else {}
        required = bool(extra.get("required_for_completion"))
        raw_value = current.get(name)
        value = None if raw_value in _EMPTY_VALUES else raw_value

        field_type = _json_type_for(field_info.annotation)
        entry: dict[str, Any] = {
            "name": name,
            "label": extra.get("label", name),
            "required": required,
            "type": field_type,
            "value": value,
        }
        if field_type == "array":
            entry["items"] = {"type": _items_type_for(field_info.annotation) or "string"}
        fields.append(entry)
    return fields
