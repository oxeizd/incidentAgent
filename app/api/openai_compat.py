from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.api.schemas import ChatCompletionRequest
from app.api.safe_errors import user_fallback_for_exception
from app.ai.schemas.orchestrator import build_initial_state


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    *,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def mount_openai_compat_endpoint(app) -> None:
    @app.post("/v1/chat/completions")
    async def chat_completions(payload: ChatCompletionRequest, request: Request):
        if not payload.stream:
            raise HTTPException(status_code=400, detail="Only stream=true is supported by this agent endpoint")

        thread_id = payload.metadata.get("thread_id") if payload.metadata else None
        if not thread_id:
            raise HTTPException(status_code=422, detail="metadata.thread_id is required")
        user_messages = [message for message in payload.messages if message.role == "user"]
        if not user_messages:
            raise HTTPException(status_code=422, detail="At least one user message is required")
        text = user_messages[-1].content or ""

        graph = request.app.state.graph
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        snapshot = await graph.aget_state(config)
        awaiting = bool(snapshot.interrupts)
        if awaiting:
            raise HTTPException(status_code=409, detail="Thread is waiting for a tool response")
        graph_input = build_initial_state(thread_id, HumanMessage(content=text)) if not snapshot.values else {"messages": [HumanMessage(content=text)]}

        async def generate() -> AsyncIterator[str]:
            completion_id = f"chatcmpl_{uuid.uuid4().hex}"
            created = int(time.time())
            yield _sse(_chunk(completion_id, created, payload.model, {"role": "assistant"}))
            emitted = ""
            try:
                async for _, mode, chunk in graph.astream(graph_input, config=config, stream_mode=["updates"], subgraphs=True):
                    if mode != "updates":
                        continue
                    for _, update in (chunk or {}).items():
                        if not isinstance(update, dict):
                            continue
                        messages = update.get("messages")
                        if not messages:
                            continue
                        last = messages[-1] if isinstance(messages, list) else messages
                        content = getattr(last, "content", None)
                        if content and content != emitted:
                            delta = content[len(emitted):] if content.startswith(emitted) else content
                            emitted = content
                            if delta:
                                yield _sse(_chunk(completion_id, created, payload.model, {"content": delta}))
                yield _sse(_chunk(completion_id, created, payload.model, {}, finish_reason="stop"))
            except Exception as exc:
                safe = user_fallback_for_exception(exc)
                yield _sse(_chunk(completion_id, created, payload.model, {"content": safe.message}))
                yield _sse(_chunk(completion_id, created, payload.model, {}, finish_reason="stop"))
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")