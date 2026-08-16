"""app/ai/presentation/timeline.py — рендер хронологии инцидента."""
from __future__ import annotations
import re
from datetime import datetime

from app.ai.presentation.text_utils import esc, month_name_ru

TL_HEADER_RE = re.compile(r'^(дата\s*(и\s*время)?\s*(события?|инцидента)?|\s*дата\s*)$', re.IGNORECASE)


def build_timeline_items(chrono_text: str, root=None) -> list:
    if not chrono_text or not chrono_text.strip():
        return ['<div class="tl-item"><div class="tl-main editable-text" contenteditable="false">Хронология не указана</div></div>']
    lines = [l.strip() for l in chrono_text.strip().split("\n") if l.strip()]
    global_year = None
    items = []
    for line in lines:
        if TL_HEADER_RE.match(line):
            continue
        m_full = re.match(r'^(\d{2}[./]\d{2}[./]\d{2,4})\s+(\d{1,2}[.:]\d{2})\s*[–\-]\s*(.*)', line)
        if m_full:
            date_str, time_str, desc = m_full.group(1), m_full.group(2), m_full.group(3).strip()
        else:
            m_time = re.match(r'^(\d{2}[./]\d{2})\.?\s+(\d{1,2}[.:]\d{2})\s*[–\-]\s*(.*)', line)
            if m_time:
                date_str, time_str, desc = m_time.group(1), m_time.group(2), m_time.group(3).strip()
            else:
                m_no_time = re.match(r'^(\d{2}[./]\d{2})\.?\s*[–\-]\s*(.*)', line)
                if m_no_time:
                    date_str, time_str, desc = m_no_time.group(1), "", m_no_time.group(2).strip()
                else:
                    items.append(f'<div class="tl-item"><div class="tl-main editable-text" contenteditable="false">{esc(line)}</div></div>')
                    continue
            if not re.search(r'\d{4}$', date_str):
                if global_year is None:
                    global_year = str(datetime.now().year)
                date_str += "." + global_year
        date_norm = date_str.replace("/", ".").split(".")
        date_label = f"{date_norm[0]} {month_name_ru(date_norm[1])} {date_norm[2]}" if len(date_norm) >= 3 else date_str
        meta = f"{esc(time_str)} • {esc(date_label)}" if time_str else esc(date_label)
        items.append(
            f'<div class="tl-item"><div class="tl-dot"></div>'
            f'<div class="tl-content"><div class="tl-meta editable-text" contenteditable="false">{meta}</div>'
            f'<div class="tl-main editable-text" contenteditable="false">{esc(desc)}</div></div></div>'
        )
    return items
