"""app/ai/graph/orchestrator/handlers/chitchat.py"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.ai.graph.orchestrator.prompts import CHITCHAT_FALLBACK_PROMPT
from app.ai.prompts.registry import get_prompt


class ChitchatHandler:
    async def execute(self, state, config, context) -> Command:
        response = await context.llm.ainvoke(
            [
                context.llm.build_system_message(
                    role_instruction=get_prompt("chitchat", fallback=CHITCHAT_FALLBACK_PROMPT),
                ),
                state["messages"][-1],
            ],
            worker_kind="supervisor",
        )
        return Command(update={"messages": [AIMessage(content=response.content)]})


handler = ChitchatHandler()
