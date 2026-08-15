"""
app/ai/nodes/presentation.py

СОВМЕСТИСТМОСТИ: раньше это был монолитный файл на 50 КБ (CSS+JS+вся
логика рендера презентации). Реализация переехала в пакет app/ai/presentation/
(constants.py/styles.py/script.py/text_utils.py/chain.py/alerts.py/
measures.py/info_strip.py/timeline.py/incident_tab.py/summary.py/page.py/
legacy.py) — маленькие файлы по одному слою каждый, см. их докстринги.

Естот файл — тонкий ре-экспорт, чтобы существующие импорты вида
`from app.ai.nodes.presentation import CSS, JS, build_single_summary,
build_incident_tab, esc` (см. app/ai/nodes/creator.py) продолжали работать
без единой правки. Новый код должен импортировать напрямую из
аpp.ai.presentation, а не из этого файла.
"""
from app.ai.presentation import (  # noqa: F401
    KEYWORDS_NA_OTSENKE, ARROW_SVG, CSS, JS,
    esc, format_losses, format_losses_display, month_name_ru,
    extract_core_system, extract_persons,
    normalize_chain_lines, render_chain_block, render_alerts_block,
    parse_measures_table, render_measures_block, render_measures_block_from_tasks,
    extract_team_name_and_person, build_top_info_strip,
    TL_HEADER_RE, build_timeline_items, build_incident_tab, build_single_summary,
    generate_html_from_data, generate_html_app,
    validate_docx_fields, analyze_docx_with_gigachat, parse_text_to_data,
)
