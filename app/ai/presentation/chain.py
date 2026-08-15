"""app/ai/presentation/chain.py — рендер блока «Цепочка событий»."""
from __future__ import annotations
import re

from app.ai.presentation.text_utils import esc


def normalize_chain_lines(chain_text: str) -> list:
    if not chain_text or not chain_text.strip():
        return []
    prepared = str(chain_text)
    prepared = re.sub(r'\s*(Корневой\s*:)', r'\n\1', prepared, flags=re.IGNORECASE)
    prepared = re.sub(r'\s*(Следствие\s*:)', r'\n\1', prepared, flags=re.IGNORECASE)
    raw_lines = [x.strip(' ;') for x in prepared.split("\n") if x.strip(' ;')]
    lines = []
    seen_root = False
    for line in raw_lines:
        line = re.sub(r'^(\d+\.\s*)', '', line).strip()
        if re.match(r'^корневой\s*:', line, re.IGNORECASE):
            rest = re.sub(r'^корневой\s*:\s*', '', line, flags=re.IGNORECASE).strip()
            if rest:
                lines.append(f"Корневой: {rest}")
                seen_root = True
        elif re.match(r'^следствие\s*:', line, re.IGNORECASE):
            rest = re.sub(r'^следствие\s*:\s*', '', line, flags=re.IGNORECASE).strip()
            if rest:
                lines.append(f"Следствие: {rest}")
        else:
            prefix = "Корневой" if not seen_root and not lines else "Следствие"
            lines.append(f"{prefix}: {line}")
            if prefix == "Корневой":
                seen_root = True
    return lines


def render_chain_block(chain_text: str) -> str:
    lines = normalize_chain_lines(chain_text)
    if not lines:
        return '<div class="detail-text editable-text" contenteditable="false">—</div>'
    html = []
    for idx, line in enumerate(lines):
        cls = "detail-text chain-line editable-text"
        if idx == 0:
            cls += " first"
        html.append(f'<div class="{cls}" contenteditable="false">{esc(line)}</div>')
    return "".join(html)
