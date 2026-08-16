"""
app/ai/graph/orchestrator/handlers/__init__.py

Локальный, декларативный catalog user-facing intents. Добавление нового
intent не требует правки giant orchestrator.py: реализуйте handler с
`execute(state, config, context) -> Command` и добавьте ровно одну запись
ниже. Dispatcher не знает деталей RCA/editor/creator.
"""
from __future__ import annotations

from app.ai.graph.orchestrator.handlers.analyze import handler as analyze_handler
from app.ai.graph.orchestrator.handlers.cancel import handler as cancel_handler
from app.ai.graph.orchestrator.handlers.chitchat import handler as chitchat_handler
from app.ai.graph.orchestrator.handlers.edit import handler as edit_handler
from app.ai.graph.orchestrator.handlers.presentation import handler as presentation_handler
from app.ai.graph.orchestrator.handlers.reanalyze import handler as reanalyze_handler
from app.ai.graph.orchestrator.handlers.resume import handler as resume_handler
from app.ai.graph.orchestrator.handlers.search import handler as search_handler


BUILTIN_INTENT_HANDLERS = {
    "new_search": search_handler,
    "search_then_analyze": search_handler,
    "analyze": analyze_handler,
    "resume_previous": resume_handler,
    "edit_report": edit_handler,
    "reanalyze_report": reanalyze_handler,
    "create_presentation": presentation_handler,
    "cancel_current": cancel_handler,
    "chitchat_or_other": chitchat_handler,
}

__all__ = ["BUILTIN_INTENT_HANDLERS"]
