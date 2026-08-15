"""app/ai/presentation/alerts.py — рендер блока «Алерты»."""
from __future__ import annotations

from app.ai.presentation.text_utils import esc


def render_alerts_block(worked_text: str, failed_text: str) -> str:
    rows = []
    for line in [x.strip() for x in (worked_text or "").split("\n") if x.strip()]:
        rows.append(
            '<div class="table-row alerts-row">'
            '<div class="alert-name editable-text" contenteditable="false">'
            '<span class="alert-marker alert-ok"></span><span>' + esc(line) + '</span></div>'
            '<div class="status-cell"><span class="status-value editable-text" contenteditable="false">Сработал</span></div></div>'
        )
    for line in [x.strip() for x in (failed_text or "").split("\n") if x.strip()]:
        rows.append(
            '<div class="table-row alerts-row">'
            '<div class="alert-name editable-text" contenteditable="false">'
            '<span class="alert-marker alert-bad"></span><span>' + esc(line) + '</span></div>'
            '<div class="status-cell"><span class="status-value editable-text" contenteditable="false">Не сработал</span></div></div>'
        )
    if not rows:
        rows.append(
            '<div class="table-row alerts-row">'
            '<div class="editable-text" contenteditable="false">—</div>'
            '<div class="status-cell"><span class="status-value editable-text" contenteditable="false">—</span></div></div>'
        )
    header = (
        '<div class="table-card" data-section="alerts">'
        '<div class="card-title editable-text" contenteditable="false">Алерты</div>'
        '<div class="table-head alerts-head"><div>Имя</div><div class="head-right"><span>Статус</span></div></div>'
    )
    return header + "".join(rows) + "</div>"
