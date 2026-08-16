import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from app.memory.repository.incidents import save_incident
from app.memory.repository.assignments import save_assignments
from app.memory.loader.mapping import map_raw_incident_to_db

logger = logging.getLogger(__name__)


def _extract_items_from_raw(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("incidents", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        for value in raw.values():
            if isinstance(value, list):
                return value
        return [raw]
    return [raw]


async def load_incidents_from_json(file_path: Path) -> None:
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return
    with open(file_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    items = _extract_items_from_raw(raw)
    logger.info("Loading %d incidents from %s", len(items), file_path)
    for idx, item in enumerate(items, 1):
        incident_data = map_raw_incident_to_db(item)
        incident_number = incident_data.get("number")
        if not incident_number:
            logger.warning("Item %d has no business_id, skipping", idx)
            continue
        await save_incident(incident_data)
        assignments_raw = item.get("assignments")
        if assignments_raw:
            await save_assignments(incident_number, assignments_raw)
        if idx % 100 == 0:
            logger.info("Loaded %d incidents", idx)
    logger.info("Completed loading %d incidents", len(items))


async def load_assignments_from_json(file_path: Path) -> None:
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return
    with open(file_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        logger.error("Ожидается объект {incident_id: [assignments]}")
        return
    for incident_id, assignments in raw.items():
        if not isinstance(assignments, list):
            logger.warning("Для %s ожидается список, получено %s", incident_id, type(assignments))
            continue
        await save_assignments(incident_id, assignments)
    logger.info("Загружены поручения для %d инцидентов", len(raw))