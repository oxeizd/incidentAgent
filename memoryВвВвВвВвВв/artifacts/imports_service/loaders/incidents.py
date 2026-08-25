from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.memory.artifacts.incidents.contracts import IncidentUpsert
from app.memory.artifacts.imports.loaders.dates import (
    parse_bool,
    parse_datetime,
    parse_float,
    parse_int,
)
from app.memory.utils import optional_text, required_text


_RESOLUTION_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "reason_inc": (
        "причина инцидента (подробно)",
        "причина инцидента",
        "причина",
    ),
    "solution": (
        "способ устранения",
        "устранение",
        "решение",
    ),
    "impact": (
        "влияние",
        "последствия",
    ),
    "start_time": (
        "фактическое время начала инцидента",
        "время начала инцидента",
    ),
    "end_time": (
        "фактическое время окончания инцидента",
        "время окончания инцидента",
    ),
}


def map_incident(raw: Mapping[str, Any]) -> IncidentUpsert:
    """
    Maps one external incident payload into its canonical artifact.

    Original resolution_description is retained. Recognized labelled parts
    are projected into dedicated fields and take priority over duplicate
    raw upstream values.
    """
    resolution_description = optional_text(
        raw.get("resolution_description")
    )
    parsed_resolution = parse_resolution_description(
        resolution_description
    )

    return IncidentUpsert(
        number=required_text(
            raw.get("business_id"),
            field_name="business_id",
        ),
        created_at=parse_datetime(raw.get("created_at")),
        target_date=parse_datetime(raw.get("target_date")),
        plan_finish_date=parse_datetime(raw.get("finish_date")),
        close_date=parse_datetime(raw.get("fact_finish_date")),
        detection_time=parse_datetime(
            raw.get("detection_time") or raw.get("created_at")
        ),
        work_group=optional_text(raw.get("itsm_work_group")),
        element_name=optional_text(
            raw.get("configuration_element")
        ),
        system_name=optional_text(raw.get("itsm_it_usluga")),
        created_by=optional_text(
            raw.get("created_by") or raw.get("initiator_id")
        ),
        executor_name=optional_text(raw.get("itsm_admin")),
        status=optional_text(raw.get("state_code_str")),
        priority_code=optional_text(raw.get("priority_code_str")),
        resolution_code=optional_text(raw.get("resolution_code")),
        registration_basis=optional_text(
            raw.get("basis_incident_registration")
        ),
        inc_type=optional_text(raw.get("inc_type")),
        stand=optional_text(raw.get("stand_type")),
        description=optional_text(raw.get("detailed_description")),
        resolution_description=resolution_description,
        reason_inc=(
            parsed_resolution.get("reason_inc")
            or optional_text(raw.get("reason_inc"))
        ),
        solution=(
            parsed_resolution.get("solution")
            or optional_text(raw.get("solution"))
        ),
        impact=(
            parsed_resolution.get("impact")
            or optional_text(raw.get("impact"))
        ),
        start_time=parse_datetime(
            parsed_resolution.get("start_time")
            or raw.get("fact_start_date")
        ),
        end_time=parse_datetime(
            parsed_resolution.get("end_time")
            or raw.get("fact_finish_date")
        ),
        impact_custom_service=parse_bool(
            raw.get("impact_custom_service")
        ),
        no_impact=parse_bool(raw.get("no_impact")),
        is_root=parse_bool(raw.get("root")),
        mttd=parse_float(raw.get("mttd")),
        mttr=parse_float(raw.get("mttr")),
        downtime=parse_float(raw.get("downtime")),
        month_created=parse_int(raw.get("month_created")),
        quarter_created=parse_int(raw.get("quarter_created")),
        ai_description=optional_text(raw.get("ai_description")),
    )


def parse_resolution_description(
    text: str | None,
) -> dict[str, str]:
    """
    Extracts labelled sections from manually authored resolution text.

    Supported forms:

        Причина инцидента (подробно): Ошибка маршрутизации
        Способ устранения:
        Переключён резервный маршрут
        Влияние: Недоступность части API
    """
    if not text:
        return {}

    collected: dict[str, list[str]] = {}
    current_field: str | None = None

    normalized_text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    for raw_line in normalized_text.split("\n"):
        line = raw_line.strip()

        field_name, inline_value = _match_resolution_heading(line)

        if field_name is not None:
            current_field = field_name
            collected.setdefault(field_name, [])

            if inline_value is not None:
                collected[field_name].append(inline_value)

            continue

        if current_field is not None and line:
            collected[current_field].append(line)

    result: dict[str, str] = {}

    for field_name, parts in collected.items():
        value = "\n".join(
            part
            for part in parts
            if part.strip()
        ).strip()

        if value:
            result[field_name] = value

    return result


def _match_resolution_heading(
    line: str,
) -> tuple[str | None, str | None]:
    if ":" not in line:
        return None, None

    stripped = (
        line.lstrip("*-•")
        .lstrip("№")
        .lstrip(" ")
        .strip()
    )

    if ":" not in stripped:
        return None, None

    raw_heading, raw_value = stripped.split(":", maxsplit=1)
    heading = _normalize_heading(raw_heading)

    for field_name, aliases in _RESOLUTION_SECTION_ALIASES.items():
        if heading in aliases:
            inline_value = raw_value.strip()
            return field_name, inline_value or None

    return None, None


def _normalize_heading(value: str) -> str:
    return " ".join(
        value.lower()
        .replace("ё", "е")
        .strip()
        .split()
    )