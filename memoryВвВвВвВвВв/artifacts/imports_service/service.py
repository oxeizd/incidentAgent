from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, TypeVar

from app.memory.artifacts.assignments.contracts import AssignmentUpsert
from app.memory.artifacts.assignments.repository import (
    AssignmentRepository,
)
from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.artifacts.incidents.repository import IncidentRepository
from app.memory.artifacts.imports.contracts import (
    ImportEntity,
    ImportErrorItem,
    ImportReport,
)
from app.memory.artifacts.imports.loaders.assignments import map_assignment
from app.memory.artifacts.imports.loaders.incidents import map_incident
from app.memory.utils import optional_text
from app.memory.vectors.indexing import VectorIndexingService


logger = logging.getLogger(__name__)

DEFAULT_INDEX_BATCH_SIZE = 100

T = TypeVar("T")


class ImportService:
    """
    Imports domain records from JSON payloads.

    This service does not know HTTP, UploadFile, filesystem, seed data,
    MemoryFacade or entity catalog. Application orchestration owns derived
    state such as catalog rebuild after a successful incident import.
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

    async def import_bytes(
        self,
        *,
        entity: ImportEntity,
        content: bytes,
        max_errors: int = 100,
    ) -> ImportReport:
        raw = _decode_json(content)

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
        warnings: list[ImportErrorItem] = []

        logger.info(
            "Starting import: entity=%s source_items=%d",
            entity,
            len(items),
        )

        if entity == "incidents":
            incidents, failed_count = await self._import_incidents(
                items=items,
                errors=errors,
                max_errors=max_errors,
            )
            await self._index_incidents(
                incidents=incidents,
                warnings=warnings,
                max_errors=max_errors,
            )
        else:
            assignments, failed_count = await self._import_assignments(
                items=items,
                errors=errors,
                max_errors=max_errors,
            )
            await self._index_assignments(
                assignments=assignments,
                warnings=warnings,
                max_errors=max_errors,
            )

        imported_count = len(items) - failed_count
        status = _resolve_status(
            total_items=len(items),
            imported_count=imported_count,
            failed_count=failed_count,
            warnings=warnings,
        )

        report = ImportReport(
            entity=entity,
            status=status,
            total_items=len(items),
            imported_count=imported_count,
            failed_count=failed_count,
            errors=errors,
            warnings=warnings,
        )

        logger.info(
            "Import completed: entity=%s status=%s total=%d "
            "imported=%d failed=%d warnings=%d",
            entity,
            report.status,
            report.total_items,
            report.imported_count,
            report.failed_count,
            len(report.warnings),
        )

        return report

    async def _import_incidents(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> tuple[list[IncidentUpsert], int]:
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
                _append_issue(
                    issues=errors,
                    max_issues=max_errors,
                    index=index,
                    code="invalid_record",
                    message=_safe_error_message(exc),
                )

        return imported, failed_count

    async def _import_assignments(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        errors: list[ImportErrorItem],
        max_errors: int,
    ) -> tuple[list[tuple[str, AssignmentUpsert]], int]:
        imported: list[tuple[str, AssignmentUpsert]] = []
        failed_count = 0

        for index, item in enumerate(items):
            try:
                assignment = map_assignment(
                    item,
                    fallback_incident_id=optional_text(
                        item.get("incident_id")
                    ),
                )
                assignment_id = (
                    await self._assignment_repository.upsert(assignment)
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
                _append_issue(
                    issues=errors,
                    max_issues=max_errors,
                    index=index,
                    code="invalid_record",
                    message=_safe_error_message(exc),
                )

        return imported, failed_count

    async def _index_incidents(
        self,
        *,
        incidents: Sequence[IncidentUpsert],
        warnings: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        for batch_index, batch in enumerate(
            _iter_batches(incidents, self._index_batch_size)
        ):
            try:
                await self._vector_indexing.index_incidents(batch)
            except Exception as exc:
                logger.exception(
                    "Incident vector indexing failed: batch=%d ids=%s",
                    batch_index,
                    _sample_incident_ids(batch),
                )
                _append_issue(
                    issues=warnings,
                    max_issues=max_errors,
                    index=None,
                    code="vector_indexing_failed",
                    message=(
                        "Incident vector indexing failed for batch "
                        f"{batch_index}: {_safe_error_message(exc)}"
                    ),
                )

    async def _index_assignments(
        self,
        *,
        assignments: Sequence[tuple[str, AssignmentUpsert]],
        warnings: list[ImportErrorItem],
        max_errors: int,
    ) -> None:
        for batch_index, batch in enumerate(
            _iter_batches(assignments, self._index_batch_size)
        ):
            try:
                await self._vector_indexing.index_assignments(batch)
            except Exception as exc:
                logger.exception(
                    "Assignment vector indexing failed: batch=%d ids=%s",
                    batch_index,
                    _sample_assignment_ids(batch),
                )
                _append_issue(
                    issues=warnings,
                    max_issues=max_errors,
                    index=None,
                    code="vector_indexing_failed",
                    message=(
                        "Assignment vector indexing failed for batch "
                        f"{batch_index}: {_safe_error_message(exc)}"
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
        logger.warning(
            "Import record rejected: entity=%s index=%d record_id=%s "
            "error_type=%s error=%s",
            entity,
            index,
            _record_id(entity=entity, item=item),
            type(exc).__name__,
            _safe_error_message(exc),
        )


def _decode_json(content: bytes) -> Any:
    if not content:
        raise ValueError("Import file is empty")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Import file must be UTF-8 JSON") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}"
        ) from exc


def _extract_items(
    *,
    entity: ImportEntity,
    raw: Any,
) -> list[Mapping[str, Any]]:
    if isinstance(raw, list):
        return _validate_mapping_items(raw)

    if not isinstance(raw, Mapping):
        raise ValueError(
            f"Expected JSON object or array for {entity}, "
            f"got {type(raw).__name__}"
        )

    for key in (entity, "items"):
        candidate = raw.get(key)

        if isinstance(candidate, list):
            return _validate_mapping_items(candidate)

    if entity == "assignments" and _is_assignment_map(raw):
        return _flatten_assignment_map(raw)

    return _validate_mapping_items([raw])


def _validate_mapping_items(
    values: Sequence[Any],
) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []

    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(
                f"Expected object at item index {index}, "
                f"got {type(value).__name__}"
            )

        result.append(value)

    return result


def _is_assignment_map(raw: Mapping[str, Any]) -> bool:
    return bool(raw) and all(
        isinstance(value, list)
        for value in raw.values()
    )


def _flatten_assignment_map(
    raw: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    flattened: list[Mapping[str, Any]] = []

    for incident_id, assignments in raw.items():
        if not isinstance(assignments, list):
            raise ValueError(
                f"Expected assignment list for key {incident_id!r}"
            )

        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                raise ValueError(
                    f"Expected assignment object under "
                    f"{incident_id!r}"
                )

            item = dict(assignment)
            item.setdefault("incident_id", str(incident_id))
            flattened.append(item)

    return flattened


def _resolve_status(
    *,
    total_items: int,
    imported_count: int,
    failed_count: int,
    warnings: Sequence[ImportErrorItem],
) -> str:
    if total_items == 0 or imported_count == 0:
        return "failed"

    if failed_count > 0 or warnings:
        return "completed_with_errors"

    return "completed"


def _iter_batches(
    values: Sequence[T],
    size: int,
) -> Iterator[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _sample_incident_ids(
    incidents: Sequence[IncidentUpsert],
) -> list[str]:
    return _sample_values(
        incident.number
        for incident in incidents
    )


def _sample_assignment_ids(
    assignments: Sequence[tuple[str, AssignmentUpsert]],
) -> list[str]:
    return _sample_values(
        assignment_id
        for assignment_id, _ in assignments
    )


def _sample_values(
    values: Iterable[str],
    *,
    limit: int = 10,
) -> list[str]:
    result: list[str] = []

    for value in values:
        result.append(value)

        if len(result) == limit:
            break

    return result


def _append_issue(
    *,
    issues: list[ImportErrorItem],
    max_issues: int,
    index: int | None,
    code: str,
    message: str,
) -> None:
    if len(issues) >= max_issues:
        return

    issues.append(
        ImportErrorItem(
            index=index,
            code=code,
            message=message[:2_000],
        )
    )


def _record_id(
    *,
    entity: ImportEntity,
    item: Mapping[str, Any],
) -> str:
    candidate_keys = (
        ("business_id", "number", "id")
        if entity == "incidents"
        else ("id", "assignment_id", "ior", "incident_id")
    )

    for key in candidate_keys:
        value = optional_text(item.get(key))

        if value is not None:
            return value

    return "<unknown>"


def _safe_error_message(exc: Exception) -> str:
    return (str(exc).strip() or type(exc).__name__)[:2_000]