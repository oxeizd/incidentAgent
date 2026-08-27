from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.ai.runtime.services import get_memory
from app.ai.workflows.search.contracts import CatalogEntityType


LOOKUP_LIMIT = 5


class LookupEntitiesInput(BaseModel):
    """Аргументы поиска canonical-сущностей во внутреннем каталоге."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Фрагмент названия сущности строго из пользовательского запроса."
        ),
    )
    entity_type: CatalogEntityType = Field(
        description=(
            "Тип сущности: system_name, work_group, executor_name "
            "или element_name."
        ),
    )


@tool("lookup_entities", args_schema=LookupEntitiesInput)
async def lookup_entities(
    query: str,
    entity_type: CatalogEntityType,
) -> str:
    """
    Ищет официальное canonical-название сущности в каталоге.

    Вызывай для явно названных систем, сервисов, продуктов, платформ,
    интеграций, контуров, групп поддержки, подразделений, сотрудников,
    серверов, БД, хостов, endpoint-ов, очередей и configuration items.
    Не вызывай для дат, номеров инцидентов, статусов, приоритетов, метрик,
    ошибок и произвольного текста.
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("lookup query must not be empty")

    lookup = await get_memory().lookup_entities(
        query=normalized_query,
        entity_type=entity_type,
        limit=LOOKUP_LIMIT,
    )

    return json.dumps(
        {
            "query": normalized_query,
            "entity_type": entity_type,
            "status": lookup.status,
            "match": (
                _serialize_candidate(lookup.match)
                if lookup.match is not None
                else None
            ),
            "candidates": [
                _serialize_candidate(candidate)
                for candidate in lookup.candidates
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _serialize_candidate(candidate: Any) -> dict[str, Any]:
    candidate_id = getattr(candidate, "id", None)
    candidate_value = getattr(candidate, "value", None)
    candidate_score = getattr(candidate, "score", None)

    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("lookup candidate has no id")

    if not isinstance(candidate_value, str) or not candidate_value.strip():
        raise ValueError("lookup candidate has no value")

    if not isinstance(candidate_score, int | float):
        raise ValueError("lookup candidate has no numeric score")

    return {
        "id": candidate_id,
        "value": candidate_value,
        "score": float(candidate_score),
    }