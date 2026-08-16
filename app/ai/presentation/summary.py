"""app/ai/presentation/summary.py — вкладка «Содержание» для одного инцидента."""
from __future__ import annotations
import html as html_module

from app.ai.presentation.constants import ARROW_SVG
from app.ai.presentation.text_utils import esc, extract_core_system, extract_persons, format_losses_display


def build_single_summary(data: dict) -> str:
    number = data.get(3, "—")
    safe_num = html_module.escape(str(number), quote=True)
    tab_id = f"tab-{safe_num}"
    as_fp = data.get("system_name", "") or extract_core_system(data.get(7, ""))
    desc = data.get(4, data.get(5, "—"))
    person = extract_persons(data.get(2, "—"))
    loss_display = format_losses_display(data.get(10, ""))
    loss_class = "loss-na-otsenke" if loss_display == "На оценке" else ""
    row = (
        f'<tr class="clickable-row" data-tab="{tab_id}">'
        f'<td class="col-id editable-text" contenteditable="false">{esc(number)}</td>'
        f'<td class="col-sys editable-text" contenteditable="false">{esc(as_fp)}</td>'
        f'<td class="col-desc"><div class="text-clamp-2 editable-text" contenteditable="false">{esc(desc)}</div></td>'
        f'<td class="col-resp"><div class="text-clamp-2 editable-text" contenteditable="false">{esc(person)}</div></td>'
        f'<td class="col-loss"><span class="plain-loss {loss_class} editable-text" contenteditable="false">{esc(loss_display)}</span></td>'
        f'<td class="col-arrow"><button class="arrow-btn" data-tab="{tab_id}" title="Открыть">{ARROW_SVG}</button></td>'
        '</tr>'
    )
    return (
        '\n<div class="tab-panel cover-tab active" id="tab-summary">\n'
        '  <div class="summary-layout">\n'
        '    <div class="summary-block" data-section="summary-incidents">\n'
        '      <div class="summary-title editable-text" contenteditable="false">Инцидент</div>\n'
        '      <table class="summary-table">\n'
        '        <thead><tr>'
        '<th class="col-id">ИОР</th><th class="col-sys">АС/ФП</th>'
        '<th class="col-desc">Суть инцидента</th><th class="col-resp">Ответственный</th>'
        '<th class="col-loss">Потери</th><th class="col-arrow"></th>'
        '</tr></thead>\n'
        f'        <tbody>{row}</tbody>\n'
        '      </table>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>'
    )
