"""app/ai/presentation/page.py — сборка полного HTML-документа презентации."""
from __future__ import annotations
import html as html_module
from typing import Any, Dict, List, Optional

from app.ai.presentation.styles import CSS
from app.ai.presentation.script import JS
from app.ai.presentation.constants import ARROW_SVG
from app.ai.presentation.text_utils import esc, extract_core_system, extract_persons, format_losses_display
from app.ai.presentation.summary import build_single_summary
from app.ai.presentation.incident_tab import build_incident_tab


def generate_html_from_data(data: dict, commission_date: str = "") -> str:
    number = data.get(3, "—")
    safe_num = html_module.escape(str(number), quote=True)
    tab_label = f"EVE-{esc(number)}" if not str(number).upper().startswith("EVE-") else esc(number)
    commission_date_esc = esc(commission_date)

    tab_buttons = (
        f'<button class="tab-btn active" data-tab="tab-summary">Содержание</button>'
        f'<button class="tab-btn" data-tab="tab-{safe_num}">{tab_label}</button>'
    )
    tab_panels = build_single_summary(data) + build_incident_tab(data, root=None)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Комиссия по инцидентам — {commission_date_esc}</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="app">
    <div class="viewport">
      <div class="shell">
        <div class="header">
          <div class="header-left">
            <div class="header-tribe">Трайб Риски Розничного Бизнеса | {commission_date_esc}</div>
            <h1 class="header-title">Комиссия по инцидентам</h1>
          </div>
          <div class="header-right">
            <div class="tabs-wrapper">
              <button class="tab-nav-btn left" title="Прокрутить влево"></button>
              <div class="tabs">{tab_buttons}</div>
              <button class="tab-nav-btn right" title="Прокрутить вправо"></button>
            </div>
          </div>
        </div>
        <div class="tab-panels">{tab_panels}</div>
      </div>
    </div>
  </div>
  <div class="edit-toolbar">
    <button id="editToggle" class="edit-btn">Ред.</button>
    <button id="restoreBtn" class="edit-btn" style="display:none;">↺</button>
    <button id="saveBtn" class="edit-btn" style="display:none;">✓</button>
  </div>
  <script>{JS}</script>
</body>
</html>"""


def generate_html_app(
    files_data: List[Dict[int, str]],
    commission_date: str,
    base_dir: str,
    orders_data: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Старая многопанельная версия. Используется при batch-генерации."""
    tabs_buttons = []
    tabs_panels = []

    agenda_rows = []
    for d in files_data:
        num = d.get(3, "—")
        safe_num = html_module.escape(str(num), quote=True)
        tab_id = f"tab-{safe_num}"
        as_fp = extract_core_system(d.get(7, ""))
        desc = d.get(4, "—")
        person = extract_persons(d.get(2, "—"))
        loss_display = format_losses_display(d.get(10, ""))
        loss_class = "loss-na-otsenke" if loss_display == "На оценке" else ""
        agenda_rows.append(
            f'<tr class="clickable-row" data-tab="{tab_id}">'
            f'<td class="col-id editable-text" contenteditable="false">{esc(num)}</td>'
            f'<td class="col-sys editable-text" contenteditable="false">{esc(as_fp)}</td>'
            f'<td class="col-desc"><div class="text-clamp-2 editable-text" contenteditable="false">{esc(desc)}</div></td>'
            f'<td class="col-resp"><div class="text-clamp-2 editable-text" contenteditable="false">{esc(person)}</div></td>'
            f'<td class="col-loss"><span class="plain-loss {loss_class} editable-text" contenteditable="false">{esc(loss_display)}</span></td>'
            f'<td class="col-arrow"><button class="arrow-btn" data-tab="{tab_id}" title="Открыть">{ARROW_SVG}</button></td>'
            f'</tr>'
        )

    tabs_buttons.append(f'<button class="tab-btn active" data-tab="tab-summary">Содержание</button>')
    tabs_panels.append(
        '\n<div class="tab-panel cover-tab active" id="tab-summary">\n'
        '  <div class="summary-layout">\n'
        '    <div class="summary-block" data-section="summary-incidents">\n'
        f'      <div class="summary-title editable-text" contenteditable="false">Инциденты ({len(files_data)})</div>\n'
        '      <table class="summary-table">\n'
        '        <thead><tr><th class="col-id">ИОР</th><th class="col-sys">АС/ФП</th>'
        '<th class="col-desc">Суть инцидента</th><th class="col-resp">Ответственный</th>'
        '<th class="col-loss">Потери</th><th class="col-arrow"></th></tr></thead>\n'
        f'        <tbody>{"".join(agenda_rows)}</tbody>\n'
        '      </table>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>'
    )

    for d in files_data:
        num = d.get(3, "—")
        safe_num = html_module.escape(str(num), quote=True)
        tab_label = f"EVE-{esc(num)}" if not str(num).upper().startswith("EVE-") else esc(num)
        tabs_buttons.append(f'<button class="tab-btn" data-tab="tab-{safe_num}">{tab_label}</button>')
        tabs_panels.append(build_incident_tab(d, root=None))

    commission_date_esc = esc(commission_date)
    tabs_html = "".join(tabs_buttons)
    panels_html = "".join(tabs_panels)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Комиссия по инцидентам — {commission_date_esc}</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="app">
    <div class="viewport">
      <div class="shell">
        <div class="header">
          <div class="header-left">
            <div class="header-tribe">Трайб Риски Розничного Бизнеса | {commission_date_esc}</div>
            <h1 class="header-title">Комиссия по инцидентам</h1>
          </div>
          <div class="header-right">
            <div class="tabs-wrapper">
              <button class="tab-nav-btn left" title="Прокрутить влево"></button>
              <div class="tabs">{tabs_html}</div>
              <button class="tab-nav-btn right" title="Прокрутить вправо"></button>
            </div>
          </div>
        </div>
        <div class="tab-panels">{panels_html}</div>
      </div>
    </div>
  </div>
  <div class="edit-toolbar">
    <button id="editToggle" class="edit-btn">Ред.</button>
    <button id="restoreBtn" class="edit-btn" style="display:none;">↺</button>
    <button id="saveBtn" class="edit-btn" style="display:none;">✓</button>
  </div>
  <script>{JS}</script>
</body>
</html>"""
