"""
app/ai/presentation/__init__.py

Пакет-замена монолитного app/ai/nodes/presentation.py (было 50 КБ одним
файлом: CSS+JS+вся логика сборки HTML вперемешку). Разложено по слоям:

  constants.py    — ARROW_SVG, KEYWORDS_NA_OTSENKE (статика)
  styles.py       — CSS (статика, не трогаем при правке логики)
  script.py       — JS (статика, не трогаем при правке логики)
  text_utils.py   — esc/format_losses/extract_* (текстовые преобразования)
  chain.py        — блок "Цепочка событий"
  alerts.py       — блок "Алерты"
  measures.py     — блоки мер/мероприятий
  info_strip.py   — верхняя инфо-полоса
  timeline.py     — хронология
  incident_tab.py — вкладка одного инцидента (собирает предыдущие блоки)
  summary.py      — вкладка "Содержание"
  page.py         — сборка полного HTML-документа (generate_html_from_data/app)
  legacy.py       — функции старого пайплайна, оставлены для совместимости

Публичный набор символов ниже — то, что реально импортируют другие модули
(creator.py и т.п.). Если добавляете новый слой — не забудьте пробросить
сюда нужные имена, иначе внешний импорт не увидит новую функцию.
"""
from app.ai.presentation.constants import KEYWORDS_NA_OTSENKE, ARROW_SVG
from app.ai.presentation.styles import CSS
from app.ai.presentation.script import JS
from app.ai.presentation.text_utils import (
    esc, format_losses, format_losses_display, month_name_ru,
    extract_core_system, extract_persons,
)
from app.ai.presentation.chain import normalize_chain_lines, render_chain_block
from app.ai.presentation.alerts import render_alerts_block
from app.ai.presentation.measures import (
    parse_measures_table, render_measures_block, render_measures_block_from_tasks,
    extract_team_name_and_person,
)
from app.ai.presentation.info_strip import build_top_info_strip
from app.ai.presentation.timeline import TL_HEADER_RE, build_timeline_items
from app.ai.presentation.incident_tab import build_incident_tab
from app.ai.presentation.summary import build_single_summary
from app.ai.presentation.page import generate_html_from_data, generate_html_app
from app.ai.presentation.legacy import validate_docx_fields, analyze_docx_with_gigachat, parse_text_to_data

__all__ = [
    "KEYWORDS_NA_OTSENKE", "ARROW_SVG", "CSS", "JS",
    "esc", "format_losses", "format_losses_display", "month_name_ru",
    "extract_core_system", "extract_persons",
    "normalize_chain_lines", "render_chain_block", "render_alerts_block",
    "parse_measures_table", "render_measures_block", "render_measures_block_from_tasks",
    "extract_team_name_and_person", "build_top_info_strip",
    "TL_HEADER_RE", "build_timeline_items", "build_incident_tab", "build_single_summary",
    "generate_html_from_data", "generate_html_app",
    "validate_docx_fields", "analyze_docx_with_gigachat", "parse_text_to_data",
]
