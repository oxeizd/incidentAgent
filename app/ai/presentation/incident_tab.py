"""app/ai/presentation/incident_tab.py — вкладка одного инцидента (main-grid)."""
from __future__ import annotations
import html as html_module
from typing import Dict

from app.ai.presentation.text_utils import esc, format_losses_display
from app.ai.presentation.chain import render_chain_block
from app.ai.presentation.alerts import render_alerts_block
from app.ai.presentation.measures import render_measures_block
from app.ai.presentation.info_strip import build_top_info_strip
from app.ai.presentation.timeline import build_timeline_items


def build_incident_tab(data: Dict[int, str], root=None) -> str:
    number = data.get(3, "—")
    desc = data.get(5, "—")
    cause = data.get(6, "—")
    oper = data.get(16, "—")
    impact = data.get(9, "—")
    chain = data.get(7, "")
    losses = data.get(10, "")
    timeline_items = "".join(build_timeline_items((data.get(18) or "").strip(), root))
    safe_num = html_module.escape(str(number), quote=True)
    return (
        f'\n<div class="tab-panel" id="tab-{safe_num}">\n'
        '  <div class="main-grid">\n'
        '    <div class="left-col">\n'
        + build_top_info_strip(data)
        + f'    <div class="detail-card" data-section="description"><div class="detail-row"><div class="detail-title editable-text" contenteditable="false">Описание</div><div class="detail-text editable-text" contenteditable="false">{esc(desc)}</div></div></div>\n'
        + f'    <div class="detail-card" data-section="cause"><div class="detail-row"><div class="detail-title editable-text" contenteditable="false">Причина</div><div class="detail-text editable-text" contenteditable="false">{esc(cause)}</div></div></div>\n'
        + f'    <div class="detail-card" data-section="chain"><div class="detail-row"><div class="detail-title editable-text" contenteditable="false">Цепочка событий</div>{render_chain_block(chain)}</div></div>\n'
        + f'    <div class="detail-card" data-section="impact"><div class="detail-row"><div class="detail-title editable-text" contenteditable="false">Влияние на продукт</div><div class="detail-text editable-text" contenteditable="false">{esc(impact)}</div></div></div>\n'
        + render_alerts_block(data.get(14, ""), data.get(15, "")) + "\n"
        + render_measures_block(oper, "Оперативные мероприятия", "opermeasures") + "\n"
        + render_measures_block(data.get(17, ""), "Системные мероприятия", "sysmeasures") + "\n"
        + '    </div>\n'
        + '    <div class="right-col">\n'
        + f'      <div class="loss-card" data-section="losses"><div class="loss-label editable-text" contenteditable="false">Потери</div><div class="loss-value editable-text" contenteditable="false">{esc(format_losses_display(losses))}</div></div>\n'
        + f'      <div class="timeline-panel" data-section="timeline"><div class="timeline-header editable-text" contenteditable="false">Хронология</div><div class="timeline-body">{timeline_items}</div></div>\n'
        + '    </div>\n'
        + '  </div>\n'
        + '</div>'
    )
