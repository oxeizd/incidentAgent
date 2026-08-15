"""app/ai/presentation/text_utils.py — базовые текстовые преобразования для верстки."""
from __future__ import annotations
import re
import html as html_module
from datetime import datetime
from typing import Any

from app.ai.presentation.constants import KEYWORDS_NA_OTSENKE


def esc(text: Any) -> str:
    if text is None:
        return "—"
    return html_module.escape(str(text or "—")).replace("\n", "<br>")


def format_losses(loss_text: str) -> str:
    if not loss_text or loss_text in ("—", "--"):
        return "На оценке"
    low = loss_text.lower()
    for kw in KEYWORDS_NA_OTSENKE:
        if kw in low:
            return "На оценке"
    return loss_text.strip()


def format_losses_display(loss_text: str) -> str:
    formatted = format_losses(loss_text)
    if formatted == "На оценке":
        return formatted
    m = re.search(r'(\d[\d\s,\.]*)', formatted)
    if m:
        return m.group(1).strip() + " ₽"
    return formatted


def month_name_ru(mm: str) -> str:
    months = {"01": "янв", "02": "фев", "03": "мар", "04": "апр", "05": "мая", "06": "июн",
              "07": "июл", "08": "авг", "09": "сен", "10": "окт", "11": "ноя", "12": "дек"}
    return months.get(mm.zfill(2), mm)


def extract_core_system(chain_text: str) -> str:
    if not chain_text or chain_text in ("—", "--"):
        return "—"
    m = re.search(r"[Кк]орневой\s*:\s*(.+?)(?:Следствие:|$)", chain_text, re.DOTALL)
    if m:
        core = m.group(1).strip()
        core = re.sub(r"INC\d+\s*", "", core, flags=re.IGNORECASE).strip(" -–>;")
        return core or "—"
    return chain_text[:120].strip() if chain_text else "—"


def extract_persons(full_team: str) -> str:
    if not full_team or "," not in full_team:
        return full_team.strip() if full_team else "—"
    parts = [p.strip() for p in full_team.split(",")[1:]]
    fios = [p for p in parts if len(p.split()) >= 2]
    if fios:
        return ", ".join(fios)
    return ", ".join(parts) if parts else "—"
