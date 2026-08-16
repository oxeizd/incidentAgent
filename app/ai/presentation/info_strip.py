"""app/ai/presentation/info_strip.py — верхняя инфо-полоса вкладки инцидента."""
from __future__ import annotations
from typing import Dict

from app.ai.presentation.text_utils import esc
from app.ai.presentation.measures import extract_team_name_and_person


def build_top_info_strip(data: Dict[int, str]) -> str:
    number = data.get(3, "—")
    team_raw = data.get(2, "—")
    team_name, responsible = extract_team_name_and_person(team_raw)
    unit = data.get(1, "—")
    stage = data.get(8, "—")
    return (
        '\n<div class="info-strip" data-section="info-strip">\n'
        f'  <div class="info-item"><div class="info-label editable-text" contenteditable="false">ИОР:</div><div class="info-value editable-text" contenteditable="false">{esc(number)}</div></div>\n'
        f'  <div class="info-item"><div class="info-label editable-text" contenteditable="false">Команда:</div><div class="info-value editable-text" contenteditable="false">{esc(team_name)}</div></div>\n'
        f'  <div class="info-item"><div class="info-label editable-text" contenteditable="false">Ответственный:</div><div class="info-value editable-text" contenteditable="false">{esc(responsible)}</div></div>\n'
        f'  <div class="info-item"><div class="info-label editable-text" contenteditable="false">Юнит:</div><div class="info-value editable-text" contenteditable="false">{esc(unit)}</div></div>\n'
        f'  <div class="info-item"><div class="info-label editable-text" contenteditable="false">Этап процесса:</div><div class="info-value editable-text" contenteditable="false">{esc(stage)}</div></div>\n'
        '</div>'
    )
