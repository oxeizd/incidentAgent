"""app/ai/presentation/measures.py — парсинг и рендер блоков мер/мероприятий."""
from __future__ import annotations
import re

from app.ai.presentation.text_utils import esc


def _extract_fio_and_deadline(text: str) -> tuple:
    """Извлекает ФИО и срок из текста вида 'отв. Нестеров В., срок 22.05'."""
    if not text or text.strip() in ("", "—", "--"):
        return "—", "—"
    raw = " ".join(text.replace("\n", " ").split())
    raw = re.sub(r'^\s*отв(?:етственный)?\.?\s*:?\s*', '', raw, flags=re.IGNORECASE)
    fio = "—"
    deadline = "—"
    fio_match = re.search(r'([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2}|[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.){1,2})', raw)
    if fio_match:
        fio = fio_match.group(1).strip()
    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4}|\d{2}\.\d{2})', raw)
    if date_match:
        deadline = f"до {date_match.group(1)}"
    return fio, deadline


def _split_measure_and_responsible(line: str) -> tuple:
    """Разделяет строку меры на (мера, ответственный)."""
    if not line or not line.strip():
        return "—", "—"
    cleaned = " ".join(line.strip().split())
    m = re.search(r'(,?\s*)(?:Отв(?:етственный)?\.?\s*:?\s*)', cleaned, re.IGNORECASE)
    if not m:
        return cleaned, "—"
    measure = cleaned[:m.start()].strip(" ;.-")
    responsible_part = cleaned[m.start():].strip()
    responsible_part = re.sub(r'^[,;\s]+', '', responsible_part)
    fio, deadline = _extract_fio_and_deadline(responsible_part)
    if fio == "—" and deadline == "—":
        responsible = "—"
    elif fio != "—" and deadline != "—":
        responsible = f"{fio}, {deadline}"
    else:
        responsible = fio if fio != "—" else deadline
    return measure or "—", responsible


def parse_measures_table(text: str) -> list:
    if not text or not text.strip():
        return []
    rows = []
    for line in [x.strip() for x in text.split("\n") if x.strip()]:
        line = re.sub(r'^(\d+\.\s*|[•\-–]\s*)', '', line).strip()
        measure, responsible = _split_measure_and_responsible(line)
        rows.append((measure, responsible))
    return rows


def render_measures_block(text: str, title: str, section_name: str, hide_responsible: bool = False) -> str:
    rows = parse_measures_table(text)
    if not rows:
        rows = [("—", "—")]
    if not hide_responsible:
        hide_responsible = all(resp.strip() in ("", "—", "--") for _, resp in rows)
    rows_html = []
    for measure, responsible in rows:
        if hide_responsible:
            rows_html.append(
                '<div class="table-row measures-row" style="grid-template-columns:1fr;">'
                '<div class="editable-text" contenteditable="false">' + esc(measure) + '</div></div>'
            )
        else:
            rows_html.append(
                '<div class="table-row measures-row">'
                '<div class="editable-text" contenteditable="false">' + esc(measure) + '</div>'
                '<div class="resp-cell editable-text" contenteditable="false">' + esc(responsible) + '</div></div>'
            )
    header = (
        '<div class="table-card" data-section="' + section_name + '">'
        '<div class="card-title editable-text" contenteditable="false">' + esc(title) + '</div>'
        '<div class="table-head measures-head"' + (' style="grid-template-columns:1fr;"' if hide_responsible else '') + '>'
        '<div>Мера</div>'
        + ('' if hide_responsible else '<div class="head-right"><span>Ответственный и срок</span></div>')
        + '</div>'
    )
    return header + "".join(rows_html) + "</div>"


def render_measures_block_from_tasks(tasks: list, title: str, section_name: str, hide_responsible: bool = False) -> str:
    if not tasks:
        return render_measures_block("", title, section_name, hide_responsible=True)
    rows = []
    for task in tasks:
        if isinstance(task, dict):
            title_text = (task.get("title") or "").strip()
            desc_text = (task.get("description") or "").strip()
            task_text = title_text
            if desc_text:
                task_text = (task_text + ": " + desc_text) if task_text else desc_text
            if not task_text:
                continue
            rows.append((task_text, "—"))
        elif isinstance(task, str) and task.strip():
            rows.append((task.strip(), "—"))
    if not rows:
        return render_measures_block("", title, section_name, hide_responsible=True)
    if not hide_responsible:
        hide_responsible = all(resp.strip() in ("", "—", "--") for _, resp in rows)
    rows_html = []
    for measure, responsible in rows:
        if hide_responsible:
            rows_html.append(
                '<div class="table-row measures-row" style="grid-template-columns:1fr;">'
                '<div class="editable-text" contenteditable="false">' + esc(measure) + '</div></div>'
            )
        else:
            rows_html.append(
                '<div class="table-row measures-row">'
                '<div class="editable-text" contenteditable="false">' + esc(measure) + '</div>'
                '<div class="resp-cell editable-text" contenteditable="false">' + esc(responsible) + '</div></div>'
            )
    header = (
        '<div class="table-card" data-section="' + section_name + '">'
        '<div class="card-title editable-text" contenteditable="false">' + esc(title) + '</div>'
        '<div class="table-head measures-head"' + (' style="grid-template-columns:1fr;"' if hide_responsible else '') + '>'
        '<div>Мера</div>'
        + ('' if hide_responsible else '<div class="head-right"><span>Ответственный и срок</span></div>')
        + '</div>'
    )
    return header + "".join(rows_html) + "</div>"


def extract_team_name_and_person(team_raw: str):
    """Разделить 'Команда, ФИО' на название команды и ответственного."""
    if not team_raw or team_raw in ("—", "--"):
        return "—", "—"
    parts = [p.strip() for p in team_raw.split(",") if p.strip()]
    team_name = parts[0] if parts else "—"
    responsible = parts[1] if len(parts) > 1 else "—"
    return team_name, responsible
