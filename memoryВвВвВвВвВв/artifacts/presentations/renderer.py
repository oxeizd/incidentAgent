from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

from app.memory.artifacts.presentations.document import (
    DASH,
    PresentationAssignment,
    PresentationDocument,
    PresentationIncident,
)


def render_presentation(document: PresentationDocument) -> str:
    """
    Рендерит self-contained HTML для PresentationDocument schema v2.

    Одна презентация может содержать много инцидентов:
    - summary содержит строку каждого ИОР;
    - каждый ИОР получает отдельную вкладку и панель;
    - поручения остаются общими для всей презентации.

    RCA-анализ временно отключён (нет вкладки и панели analysis).
    """
    tabs = _tabs(document)
    panels = [
        _summary_panel(document),
        *[
            _incident_panel(incident, index)
            for index, incident in enumerate(
                document.incidents,
                start=1,
            )
        ],
        *[
            _assignment_panel(assignment, index)
            for index, assignment in enumerate(
                document.assignments,
                start=1,
            )
        ],
    ]

    title = _text(
        document.incidents[0].brief
        if document.incidents
        else "Презентация по инцидентам"
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — презентация</title>
  <style>
    :root {{
      --bg-top: #f3f3f1;
      --bg-bottom: #d9def7;
      --text: #242424;
      --muted: #5e5a56;
      --line: #e8e3dd;
      --tab-1: #98aafc;
      --tab-2: #8398f6;
      --timeline: #223038;
      --card: rgba(255, 255, 255, .78);
      --good: #3aa66a;
      --bad: #e56363;
      font-family: "Segoe UI", Arial, sans-serif;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      min-width: 1060px;
      background: #e7eaf4;
      color: var(--text);
    }}

    .vp-root {{
      width: min(1440px, calc(100vw - 42px));
      min-height: calc(100vh - 42px);
      margin: 21px auto;
      padding: 24px 28px 30px;
      border-radius: 16px;
      background: linear-gradient(
        180deg,
        var(--bg-top) 36%,
        var(--bg-bottom) 100%
      );
      box-shadow: 0 16px 44px rgba(43, 52, 84, .18);
    }}

    .vp-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 16px;
    }}

    .vp-tribe {{
      font-size: 15px;
      color: #5e5a56;
      margin-bottom: 5px;
    }}

    .vp-title {{
      margin: 0;
      font-size: 27px;
      font-weight: 700;
    }}

    .vp-tabs-wrapper {{
      position: relative;
      display: flex;
      align-items: center;
      max-width: 720px;
    }}

    .vp-tabs {{
      display: flex;
      align-items: center;
      gap: 8px;
      overflow-x: auto;
      scroll-behavior: smooth;
      scrollbar-width: none;
      padding: 3px 5px;
    }}

    .vp-tabs::-webkit-scrollbar {{
      display: none;
    }}

    .vp-tab {{
      flex: 0 0 auto;
      border: 1px solid rgba(217, 220, 231, .9);
      background: rgba(255, 255, 255, .96);
      border-radius: 12px;
      padding: 9px 14px;
      color: #3d3b39;
      font: inherit;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    }}

    .vp-tab.active {{
      color: #fff;
      background: linear-gradient(
        180deg,
        var(--tab-1),
        var(--tab-2)
      );
      border-color: transparent;
      box-shadow: 0 7px 15px rgba(131, 152, 246, .28);
    }}

    .vp-nav {{
      display: none;
      align-items: center;
      justify-content: center;
      width: 38px;
      height: 38px;
      flex: 0 0 auto;
      border: 1px solid #e0e4ee;
      border-radius: 12px;
      background: #fff;
      color: #283878;
      font-size: 23px;
      line-height: 1;
      cursor: pointer;
    }}

    .vp-nav.visible {{
      display: flex;
    }}

    .vp-nav.left {{
      margin-right: 5px;
    }}

    .vp-nav.right {{
      margin-left: 5px;
    }}

    .vp-panel {{
      display: none;
    }}

    .vp-panel.active {{
      display: block;
    }}

    .vp-summary-title {{
      font-size: 18px;
      font-weight: 700;
      color: #4e5965;
      margin: 10px 0;
    }}

    .vp-summary-table-wrap {{
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255, 255, 255, .72);
    }}

    .vp-summary-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}

    .vp-summary-table th,
    .vp-summary-table td {{
      text-align: left;
      padding: 14px 16px;
      border-bottom: 1px solid #e3e8ef;
      font-size: 15px;
      line-height: 1.5;
      vertical-align: top;
    }}

    .vp-summary-table th {{
      color: #5b6472;
      font-size: 13px;
      letter-spacing: .02em;
    }}

    .vp-summary-table tr:last-child td {{
      border-bottom: 0;
    }}

    .clickable-row {{
      cursor: pointer;
    }}

    .clickable-row:hover {{
      background: rgba(152, 170, 252, .12);
    }}

    .vp-arrow {{
      color: #5367ca;
      font-size: 22px;
      text-align: center;
    }}

    .vp-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 330px;
      gap: 14px;
    }}

    .vp-left,
    .vp-right {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-width: 0;
    }}

    .vp-info-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }}

    .vp-info {{
      background: rgba(255, 255, 255, .96);
      border: 1px solid #e3e7ef;
      border-radius: 12px;
      padding: 11px 13px;
    }}

    .vp-info-label {{
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 5px;
      color: #5b6472;
    }}

    .vp-info-value {{
      font-size: 15px;
      word-break: break-word;
    }}

    .vp-card,
    .vp-loss {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}

    .vp-card-title {{
      padding: 13px 15px 9px;
      font-size: 16px;
      font-weight: 700;
    }}

    .vp-text {{
      padding: 0 15px 15px;
      font-size: 15px;
      line-height: 1.55;
      white-space: pre-wrap;
    }}

    .vp-chain {{
      padding: 9px 15px;
      border-top: 1px solid #f0ece7;
      white-space: pre-wrap;
      font-size: 14px;
      line-height: 1.5;
    }}

    .vp-chain.first {{
      border-top: none;
    }}

    .vp-mhead,
    .vp-mrow {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 160px;
      gap: 8px;
      padding-left: 15px;
      padding-right: 15px;
      font-size: 14px;
    }}

    .vp-mhead {{
      color: #5f5a54;
      padding-bottom: 7px;
      font-weight: 600;
    }}

    .vp-mrow {{
      padding-top: 10px;
      padding-bottom: 10px;
      border-top: 1px solid #f0ece7;
    }}

    .vp-mrow.clickable {{
      width: 100%;
      border-left: 0;
      border-right: 0;
      border-bottom: 0;
      color: inherit;
      background: transparent;
      text-align: left;
      font: inherit;
      cursor: pointer;
    }}

    .vp-mrow.clickable:hover {{
      color: #4359c5;
      background: rgba(152, 170, 252, .12);
    }}

    .vp-measure-title {{
      display: block;
      font-weight: 650;
    }}

    .vp-measure-note {{
      display: block;
      margin-top: 3px;
      color: #6c7480;
      font-size: 12px;
    }}

    .vp-alerts {{
      display: grid;
    }}

    .vp-alert {{
      display: grid;
      grid-template-columns: 5px minmax(0, 1fr) 140px;
      gap: 10px;
      padding: 10px 15px;
      border-top: 1px solid #f0ece7;
      font-size: 14px;
      line-height: 1.4;
    }}

    .vp-alert:first-child {{
      border-top: 0;
    }}

    .vp-alert-mark {{
      height: 21px;
      border-radius: 4px;
    }}

    .vp-alert-mark.ok {{
      background: var(--good);
    }}

    .vp-alert-mark.bad {{
      background: var(--bad);
    }}

    .vp-alert-status {{
      color: #6c7480;
      font-size: 13px;
    }}

    .vp-loss {{
      padding: 16px;
    }}

    .vp-loss-label {{
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 8px;
    }}

    .vp-loss-value {{
      font-size: 34px;
      color: #64748b;
    }}

    .vp-timeline {{
      min-height: 310px;
      background: linear-gradient(
        180deg,
        var(--timeline),
        #24323a
      );
      color: #fff;
      border-radius: 14px;
      padding: 15px;
    }}

    .vp-timeline-header {{
      font-weight: 700;
      margin-bottom: 11px;
      font-size: 16px;
    }}

    .vp-timeline-body {{
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 13px;
      padding-left: 19px;
    }}

    .vp-timeline-body::before {{
      content: "";
      position: absolute;
      left: 4px;
      top: 4px;
      bottom: 4px;
      width: 2px;
      background: rgba(255, 255, 255, .78);
    }}

    .vp-tl-item {{
      position: relative;
    }}

    .vp-tl-dot {{
      position: absolute;
      left: -19px;
      top: 5px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #f4f0e5;
    }}

    .vp-tl-meta {{
      font-size: 12px;
      color: rgba(255, 255, 255, .72);
      margin-bottom: 4px;
    }}

    .vp-tl-main {{
      font-size: 14px;
      font-weight: 600;
      line-height: 1.38;
    }}

    .assignment-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) 340px;
      gap: 14px;
    }}

    .assignment-status {{
      display: inline-block;
      margin: 18px 16px 4px;
      padding: 5px 10px;
      border-radius: 99px;
      color: #956000;
      background: #fff0d6;
      font-size: 12px;
      font-weight: 750;
    }}

    .assignment-title {{
      margin: 0;
      padding: 0 16px;
      font-size: 23px;
      line-height: 1.25;
    }}

    .assignment-description {{
      padding: 0 16px 17px;
      color: #43484f;
      font-size: 15px;
      line-height: 1.55;
    }}

    .assignment-detail {{
      padding: 13px 16px;
      border-top: 1px solid #f0ece7;
    }}

    .assignment-label {{
      margin-bottom: 6px;
      color: #5e5a56;
      font-size: 13px;
      font-weight: 650;
    }}

    .assignment-value {{
      font-size: 15px;
      line-height: 1.52;
      white-space: pre-wrap;
    }}

    .assignment-value.empty {{
      color: #64748b;
    }}

    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 9px;
      padding: 15px;
    }}

    .meta {{
      min-height: 76px;
      padding: 11px;
      border-radius: 11px;
      background: rgba(238, 241, 248, .78);
    }}

    .meta-label {{
      margin-bottom: 5px;
      color: #636b78;
      font-size: 12px;
      font-weight: 700;
    }}

    .meta-value {{
      font-size: 14px;
      font-weight: 650;
      line-height: 1.35;
    }}

    @media (max-width: 900px) {{
      .vp-grid,
      .vp-info-strip,
      .assignment-layout {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="vp-root">
    <header class="vp-header">
      <div>
        <div class="vp-tribe">Трайб Риски Розничного Бизнеса</div>
        <h1 class="vp-title">Комиссия по инцидентам</h1>
      </div>

      <div class="vp-tabs-wrapper">
        <button class="vp-nav left" aria-label="Предыдущие вкладки">‹</button>
        <nav class="vp-tabs" aria-label="Разделы презентации">
          {tabs}
        </nav>
        <button class="vp-nav right" aria-label="Следующие вкладки">›</button>
      </div>
    </header>

    {"".join(panels)}
  </main>

  <script>
    const tabs = [...document.querySelectorAll(".vp-tab")];
    const panels = [...document.querySelectorAll(".vp-panel")];
    const strip = document.querySelector(".vp-tabs");
    const left = document.querySelector(".vp-nav.left");
    const right = document.querySelector(".vp-nav.right");

    function updateNavigation() {{
      const max = strip.scrollWidth - strip.clientWidth;
      left.classList.toggle("visible", strip.scrollLeft > 8);
      right.classList.toggle("visible", max - strip.scrollLeft > 8);
    }}

    function show(name) {{
      panels.forEach(panel => {{
        panel.classList.toggle("active", panel.dataset.panel === name);
      }});

      tabs.forEach(tab => {{
        tab.classList.toggle("active", tab.dataset.tab === name);
      }});

      const active = tabs.find(tab => tab.dataset.tab === name);
      active?.scrollIntoView({{
        behavior: "smooth",
        inline: "center",
        block: "nearest",
      }});

      setTimeout(updateNavigation, 250);
    }}

    tabs.forEach(tab => {{
      tab.addEventListener("click", () => show(tab.dataset.tab));
    }});

    document.querySelectorAll("[data-open-tab]").forEach(element => {{
      element.addEventListener("click", () => show(element.dataset.openTab));
    }});

    left.addEventListener("click", () => {{
      strip.scrollBy({{ left: -250, behavior: "smooth" }});
    }});

    right.addEventListener("click", () => {{
      strip.scrollBy({{ left: 250, behavior: "smooth" }});
    }});

    strip.addEventListener("scroll", updateNavigation);
    window.addEventListener("resize", updateNavigation);
    updateNavigation();
  </script>
</body>
</html>
"""


def _tabs(document: PresentationDocument) -> str:
    tabs = [
        _tab("summary", "Содержание", active=True),
        *[
            _tab(
                f"incident-{index}",
                f"Инцидент {index}",
            )
            for index, _ in enumerate(
                document.incidents,
                start=1,
            )
        ],
        *[
            _tab(
                f"assignment-{index}",
                f"Поручение {index}",
            )
            for index, _ in enumerate(
                document.assignments,
                start=1,
            )
        ],
    ]

    return "".join(tabs)


def _tab(
    tab_id: str,
    label: str,
    *,
    active: bool = False,
) -> str:
    active_class = " active" if active else ""

    return (
        f'<button class="vp-tab{active_class}" '
        f'data-tab="{escape(tab_id, quote=True)}">'
        f"{_text(label)}"
        "</button>"
    )


def _summary_panel(document: PresentationDocument) -> str:
    incident_rows = "".join(
        _incident_summary_row(incident, index)
        for index, incident in enumerate(
            document.incidents,
            start=1,
        )
    ) or _empty_row(
        "Инциденты не добавлены.",
        columns=6,
    )

    assignment_rows = "".join(
        _assignment_summary_row(assignment, index)
        for index, assignment in enumerate(
            document.assignments,
            start=1,
        )
    ) or _empty_row(
        "Нет связанных поручений.",
        columns=6,
    )

    return f"""
<section class="vp-panel active" data-panel="summary">
  <h2 class="vp-summary-title">Инциденты</h2>
  <div class="vp-summary-table-wrap">
    <table class="vp-summary-table">
      <thead>
        <tr>
          <th style="width:15%">ИОР</th>
          <th style="width:24%">АС/ФП</th>
          <th style="width:27%">Суть инцидента</th>
          <th style="width:18%">Команда</th>
          <th style="width:12%">Потери</th>
          <th style="width:4%"></th>
        </tr>
      </thead>
      <tbody>
        {incident_rows}
      </tbody>
    </table>
  </div>

  <h2 class="vp-summary-title">Поручения</h2>
  <div class="vp-summary-table-wrap">
    <table class="vp-summary-table">
      <thead>
        <tr>
          <th style="width:34%">Поручение</th>
          <th style="width:14%">Статус</th>
          <th style="width:14%">Создано</th>
          <th style="width:14%">Выполнить до</th>
          <th style="width:20%">Ответственный</th>
          <th style="width:4%"></th>
        </tr>
      </thead>
      <tbody>
        {assignment_rows}
      </tbody>
    </table>
  </div>
</section>
"""


def _incident_summary_row(
    incident: PresentationIncident,
    index: int,
) -> str:
    return f"""
<tr class="clickable-row" data-open-tab="incident-{index}">
  <td>{_text(incident.number)}</td>
  <td>{_core_system(incident.chain)}</td>
  <td>{_text(incident.brief)}</td>
  <td>{_text(incident.team)}</td>
  <td>{_text(incident.losses)}</td>
  <td class="vp-arrow">›</td>
</tr>
"""


def _incident_panel(
    incident: PresentationIncident,
    index: int,
) -> str:
    system_measures = (
        _measure_row(
            incident.systemic_measures,
            DASH,
        )
    )

    return f"""
<section class="vp-panel" data-panel="incident-{index}">
  <div class="vp-grid">
    <div class="vp-left">
      <div class="vp-info-strip">
        {_info("ИОР", incident.number)}
        {_info("Команда", incident.team)}
        {_info("Юнит", incident.unit)}
        {_info("Этап процесса", incident.stage)}
      </div>

      {_extra_fields(incident)}
      {_card("Описание", incident.description)}
      {_card("Причина", incident.cause)}
      {_chain_card(incident.chain)}
      {_card("Влияние на продукт", incident.impact)}
      {_alerts_card(incident)}
      {_measures_card(
          "Оперативные мероприятия",
          _measure_row(incident.operational_measures, DASH),
      )}
      {_measures_card("Системные мероприятия", system_measures)}
    </div>

    <aside class="vp-right">
      <article class="vp-loss">
        <div class="vp-loss-label">Потери</div>
        <div class="vp-loss-value">{_text(incident.losses)}</div>
      </article>

      {_timeline(incident.timeline)}
    </aside>
  </div>
</section>
"""


def _extra_fields(
    incident: PresentationIncident,
) -> str:
    if not incident.extra_fields:
        return ""

    fields = "".join(
        _info(field.label, field.value)
        for field in incident.extra_fields
    )

    return f'<div class="vp-info-strip">{fields}</div>'


def _assignment_panel(
    assignment: PresentationAssignment,
    index: int,
) -> str:
    return f"""
<section class="vp-panel" data-panel="assignment-{index}">
  <div class="assignment-layout">
    <article class="vp-card">
      <span class="assignment-status">{_text(assignment.status)}</span>
      <h2 class="assignment-title">{_text(assignment.title)}</h2>
      <p class="assignment-description">{_text(assignment.description)}</p>

      {_assignment_detail("Устраняет", assignment.addresses)}
      {_assignment_detail(
          "Ожидаемый результат",
          assignment.expected_result,
      )}
      {_assignment_detail(
          "Результат выполнения",
          assignment.result or "Пока не указан.",
          empty=assignment.result is None,
      )}
    </article>

    <aside class="vp-card">
      <div class="vp-card-title">Реквизиты поручения</div>
      <div class="meta-grid">
        {_meta("Приоритет", assignment.priority)}
        {_meta("Дата создания", assignment.created_at)}
        {_meta("Выполнить до", assignment.deadline_at)}
        {_meta("Ответственный", assignment.responsible)}
        {_meta("Статус", assignment.status)}
        {_meta("Тип", assignment.type)}
      </div>
    </aside>
  </div>
</section>
"""


def _assignment_summary_row(
    assignment: PresentationAssignment,
    index: int,
) -> str:
    return f"""
<tr class="clickable-row" data-open-tab="assignment-{index}">
  <td>{_text(assignment.title)}</td>
  <td>{_text(assignment.status)}</td>
  <td>{_text(assignment.created_at)}</td>
  <td>{_text(assignment.deadline_at)}</td>
  <td>{_text(assignment.responsible)}</td>
  <td class="vp-arrow">›</td>
</tr>
"""


def _measure_row(
    measure: str,
    responsible: str | None,
) -> str:
    return f"""
<div class="vp-mrow">
  <span>{_text(measure)}</span>
  <span>{_text(responsible)}</span>
</div>
"""


def _measures_card(
    title: str,
    rows: str,
) -> str:
    return f"""
<article class="vp-card">
  <div class="vp-card-title">{_text(title)}</div>
  <div class="vp-mhead">
    <span>Мера</span>
    <span>Ответственный и срок</span>
  </div>
  {rows}
</article>
"""


def _alerts_card(
    incident: PresentationIncident,
) -> str:
    rows = [
        _alert_row(alert.text, "Сработал", "ok")
        for alert in incident.alerts.worked
    ]
    rows.extend(
        _alert_row(alert.text, "Не сработал", "bad")
        for alert in incident.alerts.failed
    )

    content = "".join(rows) or (
        '<div class="vp-text">Данные об алертах не указаны.</div>'
    )

    return f"""
<article class="vp-card">
  <div class="vp-card-title">Алерты</div>
  <div class="vp-alerts">{content}</div>
</article>
"""


def _alert_row(
    text: str,
    status: str,
    marker: str,
) -> str:
    return f"""
<div class="vp-alert">
  <span class="vp-alert-mark {marker}"></span>
  <span>{_text(text)}</span>
  <span class="vp-alert-status">{_text(status)}</span>
</div>
"""


def _chain_card(chain: str) -> str:
    lines = [
        line.strip()
        for line in chain.splitlines()
        if line.strip()
    ] or [DASH]

    rendered = "".join(
        (
            f'<div class="vp-chain{" first" if index == 0 else ""}">'
            f"{_text(line)}"
            "</div>"
        )
        for index, line in enumerate(lines)
    )

    return f"""
<article class="vp-card">
  <div class="vp-card-title">Цепочка событий</div>
  {rendered}
</article>
"""


def _timeline(events: list[str]) -> str:
    rows = "".join(
        _timeline_row(event)
        for event in events
    ) or (
        '<div class="vp-tl-item">'
        '<span class="vp-tl-dot"></span>'
        '<div class="vp-tl-main">Хронология не указана.</div>'
        "</div>"
    )

    return f"""
<section class="vp-timeline">
  <div class="vp-timeline-header">Хронология</div>
  <div class="vp-timeline-body">{rows}</div>
</section>
"""


def _timeline_row(event: str) -> str:
    timestamp, description = _split_timeline_event(event)

    meta = (
        f'<div class="vp-tl-meta">{_text(timestamp)}</div>'
        if timestamp
        else ""
    )

    return f"""
<div class="vp-tl-item">
  <span class="vp-tl-dot"></span>
  {meta}
  <div class="vp-tl-main">{_text(description)}</div>
</div>
"""


def _split_timeline_event(
    event: str,
) -> tuple[str, str]:
    normalized = event.strip()

    for separator in (" — ", " – ", " - "):
        if separator in normalized:
            timestamp, description = normalized.split(
                separator,
                1,
            )
            return timestamp.strip(), description.strip()

    return "", normalized


def _info(
    label: str,
    value: str | None,
) -> str:
    return f"""
<div class="vp-info">
  <div class="vp-info-label">{_text(label)}</div>
  <div class="vp-info-value">{_text(value)}</div>
</div>
"""


def _card(
    title: str,
    text: str,
) -> str:
    return f"""
<article class="vp-card">
  <div class="vp-card-title">{_text(title)}</div>
  <div class="vp-text">{_text(text)}</div>
</article>
"""


def _assignment_detail(
    label: str,
    value: str,
    *,
    empty: bool = False,
) -> str:
    empty_class = " empty" if empty else ""

    return f"""
<div class="assignment-detail">
  <div class="assignment-label">{_text(label)}</div>
  <div class="assignment-value{empty_class}">{_text(value)}</div>
</div>
"""


def _meta(
    label: str,
    value: str | None,
) -> str:
    return f"""
<div class="meta">
  <div class="meta-label">{_text(label)}</div>
  <div class="meta-value">{_text(value)}</div>
</div>
"""


def _core_system(chain: str) -> str:
    for line in chain.splitlines():
        normalized = line.strip()

        if normalized.casefold().startswith("корневой:"):
            return normalized.split(
                ":",
                1,
            )[1].strip() or DASH

    lines = chain.splitlines()
    return lines[0].strip() if lines else DASH


def _text(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return DASH

    return escape(value.strip())


def _empty_row(
    text: str,
    *,
    columns: int,
) -> str:
    return (
        "<tr>"
        f'<td colspan="{columns}" '
        'style="text-align:center;color:#74869a;padding:12px">'
        f"{_text(text)}"
        "</td>"
        "</tr>"
    )