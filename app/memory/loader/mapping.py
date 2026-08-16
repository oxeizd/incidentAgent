import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def parse_resolution_description(text: Optional[str]) -> Dict[str, Optional[str]]:
    if not text:
        return {}
    result = {}
    patterns = {
        "reason_inc": r"Причина инцидента\s*\(подробно\)\s*:\s*(.+?)(?:\n|$)",
        "solution": r"Способ устранения\s*:\s*(.+?)(?:\n|$)",
        "impact": r"Влияние\s*:\s*(.+?)(?:\n|$)",
        "start_time": r"Фактическое время начала инцидента\s*:\s*(.+?)(?:\n|$)",
        "end_time": r"Фактическое время окончания инцидента\s*:\s*(.+?)(?:\n|$)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip()
            if value:
                result[key] = value
    return result


def map_raw_incident_to_db(raw: Dict[str, Any]) -> Dict[str, Any]:
    parsed = parse_resolution_description(raw.get("resolution_description"))
    return {
        "number": raw.get("business_id"),
        "created_at": raw.get("created_at"),
        "target_date": raw.get("target_date"),
        "plan_finish_date": raw.get("finish_date"),
        "close_date": raw.get("fact_finish_date"),
        "detection_time": raw.get("created_at"),
        "work_group": raw.get("itsm_work_group"),
        "element_name": raw.get("configuration_element"),
        "system_name": raw.get("itsm_it_usluga"),
        "created_by": raw.get("created_by") or raw.get("initiator_id"),
        "executor_name": raw.get("itsm_admin"),
        "status": raw.get("state_code_str"),
        "description": raw.get("detailed_description"),
        "reason_inc": parsed.get("reason_inc") or raw.get("reason_inc"),
        "solution": parsed.get("solution") or raw.get("solution"),
        "impact": parsed.get("impact") or raw.get("impact"),
        "start_time": parsed.get("start_time") or raw.get("fact_start_date"),
        "end_time": parsed.get("end_time") or raw.get("fact_finish_date"),
        "priority_code": raw.get("priority_code_str"),
        "resolution_code": raw.get("resolution_code"),
        "registration_basis": raw.get("basis_incident_registration"),
        "inc_type": raw.get("inc_type"),
        "impact_custom_service": 1 if raw.get("impact_custom_service") else 0,
        "no_impact": 1 if raw.get("no_impact") else 0,
        "stand": raw.get("stand_type"),
        "mttd": raw.get("mttd", 0.0),
        "mttr": raw.get("mttr", 0.0),
        "downtime": raw.get("downtime", 0.0),
        "is_root": 1 if raw.get("root") else 0,
        "month_created": raw.get("month_created"),
        "quarter_created": raw.get("quarter_created"),
        "ai_description": None,
    }