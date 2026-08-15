"""
app/ai/presentation/legacy.py — функции, оставленные только для обратной
совместимости со старым (не-LangGraph) пайплайном commission_app_v9.
Новый код (creator.py и т.п.) их не вызывает.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional


def validate_docx_fields(data: Dict[int, str]) -> List[str]:
    remarks = []
    required = {5: "Описание", 6: "Причина", 9: "Влияние", 10: "Потери",
                16: "Оперативные мероприятия", 17: "Системные мероприятия", 18: "Хронология"}
    for key, name in required.items():
        val = data.get(key, "").strip()
        if not val or val == "—":
            remarks.append(f"Поле '{name}' пустое или формальное.")
    return remarks


def analyze_docx_with_gigachat(data: Dict[int, str], filename: str = None) -> Optional[str]:
    return None


def parse_text_to_data(raw_text: str) -> Dict[int, str]:
    data = {i: "—" for i in range(1, 19)}
    patterns = {
        3: r"(?:ИОР|№ инцидента|Номер)[\s:]*([A-Za-z0-9\-_]+)",
        1: r"(?:Юнит|Ответственный юнит)[\s:]*([^\n]+)",
        2: r"(?:Команда и ФИО|ФИО|Ответственный)[\s:]*([^\n]+)",
        4: r"(?:Суть инцидента|Суть)[\s:]*([^\n]+)",
        5: r"(?:Описание инцидента|Описание)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
        6: r"(?:Причина|Корневая причина)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
        7: r"(?:Цепочка событий)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
        8: r"(?:Этап процесса|Этап производственного процесса)[\s:]*([^\n]+)",
        9: r"(?:Влияние на продукт|Влияние)[\s:]*([^\n]+)",
        10: r"(?:Последствия|Оценка эффекта|Потери)[\s:]*([^\n]+)",
        16: r"(?:Оперативные мероприятия)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
        17: r"(?:Системные мероприятия)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
        18: r"(?:Хронология|Таймлайн)[\s:]*([^\n]+(?:\n[^\n]+)*?)(?=\n\n|\Z)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, raw_text, re.DOTALL | re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                data[key] = val
    return data
