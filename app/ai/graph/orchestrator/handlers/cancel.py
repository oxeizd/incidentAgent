"""app/ai/graph/orchestrator/handlers/cancel.py"""
from __future__ import annotations
from langchain_core.messages import AIMessage
from langgraph.types import Command


class CancelHandler:
    async def execute(self, state, config, context) -> Command:
        return Command(update={
            "messages": [AIMessage(content="хорошо, отменил текущую задачу.")],
            "focus_worker_id": None, "pending_interrupt": None,
        })


handler = CancelHandler()
