from __future__ import annotations

from typing import Any

from app.memory.artifacts.assignments.search import AssignmentSearch
from app.memory.artifacts.incidents.search import IncidentSearch
from app.memory.search.queries import (
    AssignmentSearchQuery,
    IncidentSearchQuery,
)
from app.memory.vectors.semantic_search import SemanticSearchService


DEFAULT_AGENT_RESULT_LIMIT = 5
MAX_AGENT_RESULT_LIMIT = 10

INCIDENT_AGENT_FIELDS = (
    "number",
    "status",
    "priority_code",
    "system_name",
    "work_group",
    "element_name",
    "description",
    "reason_inc",
    "solution",
    "impact",
    "start_time",
    "end_time",
    "mttd",
    "mttr",
    "downtime",
)

ASSIGNMENT_AGENT_FIELDS = (
    "id",
    "incident_id",
    "ior",
    "task",
    "assignment",
    "responsible",
    "deadline",
    "assigned_at",
    "status",
)


class AgentRetrievalService:
    """
    Read-only search adapter для AI graph.

    В отличие от SearchOrchestrationService и
    SemanticSearchOrchestrationService:

    - не создаёт search_results snapshots;
    - не пишет messages;
    - не требует thread_id / user_id;
    - возвращает только bounded и allowlisted данные для LLM/RCA.

    Поиск для интерфейса остаётся в SearchOrchestrationService.
    """

    def __init__(
        self,
        *,
        incident_search: IncidentSearch,
        assignment_search: AssignmentSearch,
        semantic_search: SemanticSearchService,
    ) -> None:
        self._incident_search = incident_search
        self._assignment_search = assignment_search
        self._semantic_search = semantic_search

    async def retrieve_incidents(
        self,
        *,
        filters: dict[str, Any],
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Structured SQL search incidents для agent/RCA.

        `filters` валидируются строго через IncidentSearchQuery. Никаких
        неизвестных полей, SQL fragments и произвольных filter operators.
        """
        query = IncidentSearchQuery.model_validate(filters)
        bounded_limit = _normalize_limit(limit)

        execution = await self._incident_search.execute(query)
        selected = execution.hits[:bounded_limit]

        return {
            "entity": "incidents",
            "mode": "structured",
            "query": execution.normalized_query,
            "result_count": len(execution.hits),
            "returned_count": len(selected),
            "results": [
                _project_incident(hit.payload)
                for hit in selected
            ],
        }

    async def retrieve_assignments(
        self,
        *,
        filters: dict[str, Any],
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Structured SQL search assignments для agent/RCA.
        """
        query = AssignmentSearchQuery.model_validate(filters)
        bounded_limit = _normalize_limit(limit)

        execution = await self._assignment_search.execute(query)
        selected = execution.hits[:bounded_limit]

        return {
            "entity": "assignments",
            "mode": "structured",
            "query": execution.normalized_query,
            "result_count": len(execution.hits),
            "returned_count": len(selected),
            "results": [
                _project_assignment(hit.payload)
                for hit in selected
            ],
        }

    async def find_similar_incidents(
        self,
        *,
        query_text: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Vector similarity по ai_description/reason_inc для agent/RCA.
        """
        bounded_limit = _normalize_limit(limit)

        execution = await self._semantic_search.similar_incidents(
            query_text=query_text,
            limit=bounded_limit,
        )

        return {
            "entity": "incidents",
            "mode": "semantic_similarity",
            "query": execution.normalized_query,
            "result_count": len(execution.hits),
            "returned_count": len(execution.hits),
            "results": [
                {
                    **_project_incident(hit.payload),
                    "distance": hit.score,
                }
                for hit in execution.hits
            ],
        }

    async def find_similar_assignments(
        self,
        *,
        query_text: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Vector similarity по тексту поручения для agent/RCA.
        """
        bounded_limit = _normalize_limit(limit)

        execution = await self._semantic_search.similar_assignments(
            query_text=query_text,
            limit=bounded_limit,
        )

        return {
            "entity": "assignments",
            "mode": "semantic_similarity",
            "query": execution.normalized_query,
            "result_count": len(execution.hits),
            "returned_count": len(execution.hits),
            "results": [
                {
                    **_project_assignment(hit.payload),
                    "distance": hit.score,
                }
                for hit in execution.hits
            ],
        }


def _normalize_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_AGENT_RESULT_LIMIT

    if value < 1:
        raise ValueError("limit must be at least 1")

    return min(value, MAX_AGENT_RESULT_LIMIT)


def _project_incident(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Allowlist вместо выдачи `SELECT *` в LLM context.

    source_payload, технические поля БД и потенциально тяжёлые тексты
    не должны автоматически попадать в промпт.
    """
    return {
        field_name: payload.get(field_name)
        for field_name in INCIDENT_AGENT_FIELDS
        if payload.get(field_name) is not None
    }


def _project_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        field_name: payload.get(field_name)
        for field_name in ASSIGNMENT_AGENT_FIELDS
        if payload.get(field_name) is not None
    }