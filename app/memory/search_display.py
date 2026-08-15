from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from app.config import memory_settings
from app.memory.search_contracts import (
    EntityName,
    EntityOutputConfig,
    SearchDisplay,
    SearchDisplayColumn,
    SearchOutputConfig,
)

_config_lock = Lock()
_config_cache: SearchOutputConfig | None = None


def load_search_output_config(*, force_reload: bool = False) -> SearchOutputConfig:
    global _config_cache
    with _config_lock:
        if _config_cache is not None and not force_reload:
            return _config_cache

        path: Path = memory_settings.search_output_config_path
        if not path.exists():
            raise RuntimeError(f"Search output config not found: {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Invalid YAML in search output config {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise RuntimeError(f"Search output config must be a mapping: {path}")

        try:
            _config_cache = SearchOutputConfig.model_validate(raw)
        except Exception as exc:
            raise RuntimeError(f"Invalid search output config {path}: {exc}") from exc
        return _config_cache


def validate_search_output_config() -> None:
    """Call at app startup: invalid display configuration must fail fast."""
    load_search_output_config(force_reload=True)


def get_page_size(requested_limit: int | None) -> int:
    config = load_search_output_config().defaults
    if requested_limit is None:
        return config.page_size
    return min(max(1, requested_limit), config.max_page_size)


def project_display(entity: EntityName, items: list[dict[str, Any]]) -> SearchDisplay:
    config = load_search_output_config()
    entity_config: EntityOutputConfig = getattr(config, entity)
    chat = entity_config.chat

    rows: list[dict[str, str]] = []
    for item in items:
        row: dict[str, str] = {}
        for field in chat.fields:
            value = item.get(field.key)
            if field.required and _is_empty(value):
                row[field.key] = config.defaults.null_value
            else:
                row[field.key] = _format_value(
                    value,
                    format_name=field.format,
                    truncate=field.truncate,
                    config=config,
                )
        rows.append(row)

    return SearchDisplay(
        title=chat.title,
        columns=[SearchDisplayColumn(key=field.key, label=field.label) for field in chat.fields],
        rows=rows,
    )


def project_analysis(entity: EntityName, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    config = load_search_output_config()
    entity_config: EntityOutputConfig = getattr(config, entity)
    return [{field: item.get(field) for field in entity_config.analysis} for item in items]


def _format_value(
    value: Any,
    *,
    format_name: str,
    truncate: int | None,
    config: SearchOutputConfig,
) -> str:
    if _is_empty(value):
        return config.defaults.null_value

    if format_name == "datetime":
        parsed = _parse_datetime(value)
        text = parsed.strftime(config.defaults.timestamp_format) if parsed else str(value)
    elif format_name == "date":
        parsed = _parse_date(value)
        text = parsed.strftime(config.defaults.date_format) if parsed else str(value)
    else:
        text = str(value)

    if truncate is not None and len(text) > truncate:
        return f"{text[: max(1, truncate - 1)].rstrip()}…"
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _is_empty(value: Any) -> bool:
    return value is None or value == ""
