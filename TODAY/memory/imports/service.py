from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from memory.artifacts.assignments.contracts import AssignmentUpsert
from memory.artifacts.assignments.repository import AssignmentRepository
from memory.artifacts.incidents.contracts import IncidentUpsert
from memory.artifacts.incidents.repository import IncidentRepository
from memory.imports.contracts import ImportEntity, ImportErrorItem, ImportReport
from memory.loaders.assignments import map_assignment
from memory.loaders.incidents import map_incident
from memory.vectors.indexing import VectorIndexingService


DEFAULT_INDEX_BATCH_SIZE = 100


class ImportService:
    """
    Import external JSON safely, record by record, then batch-index successes.

    The import does not create business FK relationships. `incident_id` on an
    assignment remains a soft reference. An indexing failure is isolated from
    domain storage: imported domain records remain available to SQL search.
    """

    def __init__(
        self,
        *,
        incident_repository: IncidentRepository,
        assignment_repository: AssignmentRepository,
        vector_indexing: VectorIndexingService,
        index_batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    ) -> None:
        if index_batch_size < 1:
            raise ValueError("index_batch_size must be at least 1")

        self._incident_repository = incident_repository
        self._assignment_repository = assignment_repository
        self._vector_indexing = vector_indexing
        self._index_batch_size = index_batch_size

    async def import_json_file(
        self,
        *,
        entity: ImportEntity,
        file_path: Path,
        max_errors: int = 100,
    ) -> ImportReport:
        if not file_path.exists():
            raise FileNotFoundError(f"Import file was not found: {file_path}")

        if max_errors < 1:
            raise ValueError("max_errors must be at least 1")

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
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
        errors: list[ImportErrorItem] = []
        imported_count = 0
        failed_count = 0

        indexed_incidents: list[IncidentUpsert] = []
        indexed_assignments: list[tuple[str, AssignmentUpsert]] = []

        for index, item in enumerate(items):
            try:
                if entity == "incidents":
                    incident = map_incident(item)
                    await self._incident_repository.upsert(incident)
                    indexed_incidents.append(incident)
                else:
                    assignment = map_assignment(
                        item,
                        fallback_incident_id=_optional_text(
                            item.get("incident_id")
                        ),
                    )
                    assignment_id = await self._assignment_repository.upsert(
                        assignment
                    )
                    indexed_assignments.append((assignment_id, assignment))

                imported_count += 1
            except Exception as exc:
                failed_count += 1
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=index,
                    message=_safe_error_message(exc),
                )

        await self._index_successful_records(
            entity=entity,
            incidents=indexed_incidents,
            assignments=indexed_assignments,
            errors=errors,
            max_errors=max_errors,
        )

        return ImportReport(
            entity=entity,
            total_items=len(items),
            imported_count=imported_count,
            failed_count=failed_count,
            errors=errors,
        )

    async def _index_successful_records(
        self,
        *,
        entity: ImportEntity,
        incidents: list[IncidentUpsert],
        assignments: list[tuple[str, AssignmentUpsert]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        """
        Index already-persisted domain rows in bounded batches.

        Index failures are reported but do not reduce imported_count: storage
        succeeded and a reindex/backfill job can repair vectors later.
        """
        if entity == "incidents":
            for batch_index, batch in enumerate(
                _batches(incidents, self._index_batch_size)
            ):
                try:
                    await self._vector_indexing.index_incidents(batch)
                except Exception as exc:
                    _append_error(
                        errors=errors,
                        max_errors=max_errors,
                        index=-(batch_index + 1),
                        message=(
                            "Incident vector indexing failed for import batch: "
                            f"{_safe_error_message(exc)}"
                        ),
                    )
            return

        for batch_index, batch in enumerate(
            _batches(assignments, self._index_batch_size)
        ):
            try:
                await self._vector_indexing.index_assignments(batch)
            except Exception as exc:
                _append_error(
                    errors=errors,
                    max_errors=max_errors,
                    index=-(batch_index + 1),
                    message=(
                        "Assignment vector indexing failed for import batch: "
                        f"{_safe_error_message(exc)}"
                    ),
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
                    f"Expected an assignment list for key {incident_id!r}"
                )

            for assignment in assignments:
                if not isinstance(assignment, Mapping):
                    raise ValueError(
                        f"Expected assignment object under {incident_id!r}"
                    )

                item = dict(assignment)
                item.setdefault("incident_id", incident_id)
                flattened.append(item)

        return flattened

    return [raw]


def _validate_mapping_items(values: Sequence[Any]) -> list[Mapping[str, Any]]:
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
    return bool(raw) and all(isinstance(value, list) for value in raw.values())


def _batches[T](
    values: Sequence[T],
    size: int,
) -> list[Sequence[T]]:
    return [
        values[index : index + size]
        for index in range(0, len(values), size)
    ]


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