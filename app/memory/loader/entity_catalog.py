import json
import logging
from pathlib import Path
from typing import Dict

from app.memory.db.database import get_db
from app.services.entity_resolver import ENTITY_TYPES

logger = logging.getLogger(__name__)


async def build_entity_catalog_from_db(out_path: str = "data/entity_catalog.json") -> Dict[str, Dict[str, str]]:
    """
    Читает уникальные значения прямо из таблицы incidents одним запросом на
    поле, а не итерируется по словарю в памяти — актуально сразу после
    load_incidents_from_json() без промежуточного держания всех инцидентов
    в оперативной памяти.
    """
    out_file = Path(out_path)

    existing: Dict[str, Dict[str, str]] = {}
    if out_file.exists():
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Не удалось прочитать существующий каталог {out_file}: {e}. Начинаю с пустого.")
            existing = {}

    catalog: Dict[str, Dict[str, str]] = {t: dict(existing.get(t, {})) for t in ENTITY_TYPES}

    conn = await get_db()
    try:
        for entity_type in ENTITY_TYPES:
            cur = await conn.execute(
                f"SELECT DISTINCT {entity_type} FROM incidents WHERE {entity_type} IS NOT NULL AND {entity_type} != ''"
            )
            rows = await cur.fetchall()
            for (value,) in rows:
                clean = value.strip()
                if not clean:
                    continue
                if clean not in catalog[entity_type]:
                    catalog[entity_type][clean] = clean
    finally:
        close = getattr(conn, "close", None)
        if close is not None:
            await close()

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2, sort_keys=True)

    for k, v in catalog.items():
        logger.info(f"Каталог '{k}': {len(v)} записей (алиасы + канонические имена)")

    return catalog