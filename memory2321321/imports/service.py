from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypeVar

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.assignments.repository import AssignmentRepository
from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.catalog.service import EntityCatalogService
from app.memory.imports.contracts import (
    ImportEntity,
    ImportErrorItem,
    ImportReport,
)
from app.memory.loaders.assignments import map_assignment
from app.memory.loaders.incidents import map_incident
from app.memory.vectors.indexing import VectorIndexingService


logger = logging.getLogger(__name__)

DEFAULT_INDEX_BATCH_SIZE = 100

T = TypeVar("T")


class ImportService:
    """
    Импортирует внешние JSON payloads по одному record и затем индексирует
    успешно сохранённые domain artifacts.

    Domain persistence, vector indexing и refresh entity catalog разделены:
    ошибка vector indexing не отменяет уже сохранённые incidents/assignments.
    """

    def __init__(
        self,
        *,
        incident_repository: IncidentRepository,
        assignment_repository: AssignmentRepository,
        vector_indexing: VectorIndexingService,
        entity_catalog: EntityCatalogService,
        index_batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    ) -> None:
        if index_batch_size < 1:
            raise ValueError("index_batch_size must be at least 1")

        self._incident_repository = incident_repository
        self._assignment_repository = assignment_repository
        self._vector_indexing = vector_indexing
        self._entity_catalog = entity_catalog
        self._index_batch_size = index_batch_size

    async def import_json_file(
        self,
        *,
        entity: ImportEntity,
        file_path: Path,
        max_errors: int = 100,
    ) -> ImportReport:
        if not file_path.exists():
            raise FileNotFoundError(
                f"Import file was not found: {file_path}"
            )

        try:
            raw = json.loads(
                file_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in import file {file_path}: {exc}"
            ) from exc

        return await self.import_data(
            entity=entity,
            raw=raw,
            max_errors=max_errors,
        )

    async def import_data(
        self,
        *,
        entity: ImportEntity,
        raw: Any,
        max_errors: int = 100,
    ) -> ImportReport:
        if max_errors < 1:
            raise ValueError("max_errors must be at least 1")

        items = _extract_items(entity=entity, raw=raw)
        logger.info(
            "Importing %d %s items (raw type: %s)",
            len(items),
            entity,
            type(raw).__name__,
        )

        errors: list[ImportErrorItem] = []

        if entity == "incidents":
            incidents, imported_count, failed_count = (
                await self._import_incidents(
                    items=items,
                    errors=errors,
                    max_errors=max_errors,
                )
            )

            await self._index_incidents(
                incidents=incidents,
                errors=errors,
                max_errors=max_errors,
            )
            await self._refresh_incident_catalog(
                incidents=incidents,
                errors=errors,
                max_errors=max_errors,
            )
        else:
            assignments, imported_count, failed_count = (
                await self._import_assignments(
                    items=items,
                    errors=errors,
                    max_errors=max_errors,
                )
            )

            await self._index_assignments(
                assignments=assignments,
                errors=errors,
                max_errors=max_errors,
            )

        logger.info(
            "Import finished: entity=%s total=%d imported=%d failed=%d "
            "warnings=%d",
            entity,
            len(items),
            imported_count,
            failed_count,
            max(0, len(errors) - failed_count),
        )

        return ImportReport(
            entity=entity,
            total_items=len(items),
            imported_count=imported_count,
            failed_count=failed_count,
            errors=errors,
        )

    async def _import_incidents(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> tuple[list[IncidentUpsert], int, int]:
        imported: list[IncidentUpsert] = []
        failed_count = 0

        for index, item in enumerate(items):
            try:
                incident = map_incident(item)
                await self._incident_repository.upsert(incident)
                imported.append(incident)
            except Exception as exc:
                failed_count += 1
                self._log_item_failure(
                    entity="incidents",
                    index=index,
                    item=item,
                    exc=exc,
                )
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=index,
                    message=_safe_error_message(exc),
                )

        return imported, len(imported), failed_count

    async def _import_assignments(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> tuple[list[tuple[str, AssignmentUpsert]], int, int]:
        imported: list[tuple[str, AssignmentUpsert]] = []
        failed_count = 0

        for index, item in enumerate(items):
            try:
                assignment = map_assignment(
                    item,
                    fallback_incident_id=_optional_text(
                        item.get("incident_id")
                    ),
                )
                assignment_id = (
                    await self._assignment_repository.upsert(
                        assignment
                    )
                )
                imported.append((assignment_id, assignment))
            except Exception as exc:
                failed_count += 1
                self._log_item_failure(
                    entity="assignments",
                    index=index,
                    item=item,
                    exc=exc,
                )
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=index,
                    message=_safe_error_message(exc),
                )

        return imported, len(imported), failed_count

    async def _index_incidents(
        self,
        *,
        incidents: Sequence[IncidentUpsert],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        for batch_index, batch in enumerate(
            _iter_batches(incidents, self._index_batch_size)
        ):
            try:
                await self._vector_indexing.index_incidents(batch)
            except Exception as exc:
                logger.exception(
                    "Incident vector indexing failed: batch=%d",
                    batch_index,
                )
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=batch_index,
                    message=(
                        "Incident vector indexing failed for batch "
                        f"{batch_index}: {_safe_error_message(exc)}"
                    ),
                )

    async def _index_assignments(
        self,
        *,
        assignments: Sequence[tuple[str, AssignmentUpsert]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        for batch_index, batch in enumerate(
            _iter_batches(assignments, self._index_batch_size)
        ):
            try:
                await self._vector_indexing.index_assignments(batch)
            except Exception as exc:
                logger.exception(
                    "Assignment vector indexing failed: batch=%d",
                    batch_index,
                )
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=batch_index,
                    message=(
                        "Assignment vector indexing failed for batch "
                        f"{batch_index}: {_safe_error_message(exc)}"
                    ),
                )

    async def _refresh_incident_catalog(
        self,
        *,
        incidents: Sequence[IncidentUpsert],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        if not incidents:
            return

        fields: tuple[
            tuple[
                Literal[
                    "system_name",
                    "work_group",
                    "executor_name",
                    "element_name",
                ],
                str,
            ],
            ...,
        ] = (
            ("system_name", "system_name"),
            ("work_group", "work_group"),
            ("executor_name", "executor_name"),
            ("element_name", "element_name"),
        )

        for entity_type, field_name in fields:
            try:
                await self._entity_catalog.refresh_values(
                    entity_type=entity_type,
                    values=(
                        getattr(incident, field_name)
                        for incident in incidents
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "Entity catalog refresh failed: entity_type=%s",
                    entity_type,
                )
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=0,
                    message=(
                        f"Entity catalog refresh failed for "
                        f"{entity_type}: {_safe_error_message(exc)}"
                    ),
                )

    def _log_item_failure(
        self,
        *,
        entity: ImportEntity,
        index: int,
        item: Mapping[str, Any],
        exc: Exception,
    ) -> None:
        logger.error(
            "Failed to import %s item %d: payload=%s error=%s",
            entity,
            index,
            json.dumps(
                item,
                ensure_ascii=False,
                default=str,
            )[:500],
            exc,
        )


def _extract_items(
    *,
    entity: ImportEntity,
    raw: Any,
) -> list[Mapping[str, Any]]:
    if isinstance(raw, list):
        return _validate_mapping_items(raw)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a JSON object or array for {entity}, "
            f"got {type(raw).__name__}"
        )

    for key in (entity, "items"):
        candidate = raw.get(key)
        if isinstance(candidate, list):
            return _validate_mapping_items(candidate)

    if entity == "assignments" and _is_assignment_map(raw):
        flattened: list[Mapping[str, Any]] = []

        for incident_id, assignments in raw.items():
            if not isinstance(assignments, list):
                raise ValueError(
                    f"Expected an assignment list for key "
                    f"{incident_id!r}"
                )

            for assignment in assignments:
                if not isinstance(assignment, Mapping):
                    raise ValueError(
                        f"Expected assignment object under "
                        f"{incident_id!r}"
                    )

                item = dict(assignment)
                item.setdefault("incident_id", incident_id)
                flattened.append(item)

        return flattened

    return [raw]


def _validate_mapping_items(
    values: Sequence[Any],
) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []

    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(
                "Expected object at item index "
                f"{index}, got {type(value).__name__}"
            )

        items.append(value)

    return items


def _is_assignment_map(raw: Mapping[str, Any]) -> bool:
    return bool(raw) and all(
        isinstance(value, list)
        for value in raw.values()
    )


def _iter_batches(
    values: Sequence[T],
    size: int,
) -> Iterator[Sequence[T]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _append_error(
    *,
    errors: list[ImportErrorItem],
    max_errors: int,
    index: int,
    message: str,
) -> None:
    if len(errors) >= max_errors:
        return

    errors.append(
        ImportErrorItem(
            index=index,
            message=message,
        )
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message[:2_000]