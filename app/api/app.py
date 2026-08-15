"""FastAPI-обвязка: единый endpoint /message + SSE-стриминг.

Включает:
- список/историю тредов и SSE chat endpoint;
- OpenAI-совместимые interrupt tool calls;
- строгую защиту от stale tool_call_id;
- человекочитаемый display_text для history;
- fail-fast валидацию data/config/search_output.yaml при старте.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.ai.graph.orchestrator import build_orchestrator_graph
from app.ai.nodes.creator import build_full_html, collected_to_incident_dict
from app.ai.registry.bootstrap import register_builtin_workflows
from app.ai.schemas.orchestrator import build_initial_state
from app.ai.tools.bootstrap import register_builtin_toolsets
from app.config import settings
from app.memory.checkpoint import close_checkpointer, get_checkpointer
from app.memory.db.database import close_db, initialize_database
from app.memory.repository.presentations import (
    delete_presentation,
    get_presentation,
    list_my_presentations,
    list_shared_presentations,
    publish_presentation,
    unpublish_presentation,
    update_presentation_fields,
)
from app.memory.repository.threads import (
    add_message as add_message_db,
    create_thread as create_thread_db,
    delete_thread as delete_thread_db,
    get_messages as get_messages_db,
    get_threads_by_user,
)
from app.memory.search_display import validate_search_output_config
from app.observability.phoenix import start_phoenix, stop_phoenix_and_save_traces

from .agui import mount_agui_endpoint
from .schemas import (
    ChatMessageRequest,
    HealthResponse,
    INTERRUPT_FUNCTION_DEFINITIONS,
    MessageRequest,
    ThreadStateResponse,
    interrupt_to_tool_call,
)
from .sse import stream_graph_events

logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))


class PresentationFieldsUpdate(BaseModel):
    fields: dict[str, Any]


class StaleInterruptError(Exception):
    """Ответ пришёл на interrupt, который уже не актуален."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Инициализация базы данных...")
    await initialize_database()

    # Если YAML содержит несуществующее поле или некорректную структуру,
    # сервис не стартует. Это исключает тихо пустые/битые результаты поиска.
    validate_search_output_config()
    logger.info("Search output config validated")

    register_builtin_workflows()
    register_builtin_toolsets()

    checkpointer = await get_checkpointer()
    logger.info("LangGraph checkpointer готов")

    if settings.PHOENIX_ENABLED:
        start_phoenix(load_existing=True)
        logger.info("Phoenix включён через настройки")
    else:
        logger.info("Phoenix отключён")

    app.state.graph = build_orchestrator_graph(checkpointer=checkpointer)
    mount_agui_endpoint(app, app.state.graph)
    app.state.thread_locks = defaultdict(asyncio.Lock)
    logger.info("Приложение запущено и готово к работе")

    yield

    logger.info("Остановка приложения: закрытие checkpointer, Phoenix и БД...")
    if settings.PHOENIX_ENABLED:
        try:
            await stop_phoenix_and_save_traces()
        except Exception as exc:
            logger.warning("Ошибка остановки Phoenix: %s", exc)
    await close_checkpointer()
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(title="Мультиагентная система разбора инцидентов", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="app/api/static"), name="static")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse("app/api/static/index.html")

    @app.get("/api/v1/interrupt-tools")
    async def interrupt_tools() -> dict[str, Any]:
        return {"tools": INTERRUPT_FUNCTION_DEFINITIONS}

    @app.get("/api/v1/threads/{thread_id}/artifacts/{artifact_id}/file")
    async def download_artifact_file(thread_id: str, artifact_id: str, request: Request) -> Response:
        graph = request.app.state.graph
        snapshot = await graph.aget_state(_config(thread_id))
        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        artifact = values.get("artifacts", {}).get(artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")

        version = artifact["versions"][artifact["current_version"]]
        html = version["sections"].get("html")
        if html is None:
            raise HTTPException(status_code=404, detail="Artifact has no downloadable file content")

        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}.html"'},
        )

    # /mine и /shared регистрируются до /{presentation_id}, иначе dynamic route
    # перехватит статические сегменты.
    @app.get("/api/v1/presentations/mine")
    async def presentations_mine(request: Request) -> dict[str, Any]:
        user_id = _require_user_id(request)
        return {"presentations": await list_my_presentations(user_id)}

    @app.get("/api/v1/presentations/shared")
    async def presentations_shared(request: Request) -> dict[str, Any]:
        _require_user_id(request)
        return {"presentations": await list_shared_presentations()}

    @app.get("/api/v1/presentations/{presentation_id}")
    async def presentation_detail(presentation_id: str, request: Request) -> dict[str, Any]:
        user_id = _require_user_id(request)
        presentation = await get_presentation(presentation_id)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if presentation["status"] != "published" and presentation["owner_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="This draft belongs to another user")
        return presentation

    @app.patch("/api/v1/presentations/{presentation_id}")
    async def presentation_update(
        presentation_id: str,
        payload: PresentationFieldsUpdate,
        request: Request,
    ) -> dict[str, str]:
        user_id = _require_user_id(request)
        ok = await update_presentation_fields(presentation_id, user_id, payload.fields)
        if not ok:
            raise HTTPException(status_code=404, detail="Presentation not found or not owned by you")
        return {"status": "ok"}

    @app.post("/api/v1/presentations/{presentation_id}/publish")
    async def presentation_publish(presentation_id: str, request: Request) -> dict[str, str]:
        user_id = _require_user_id(request)
        ok = await publish_presentation(presentation_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Presentation not found or not owned by you")
        return {"status": "published"}

    @app.post("/api/v1/presentations/{presentation_id}/unpublish")
    async def presentation_unpublish(presentation_id: str, request: Request) -> dict[str, str]:
        user_id = _require_user_id(request)
        ok = await unpublish_presentation(presentation_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Presentation not found or not owned by you")
        return {"status": "draft"}

    @app.delete("/api/v1/presentations/{presentation_id}")
    async def presentation_delete(presentation_id: str, request: Request) -> dict[str, str]:
        user_id = _require_user_id(request)
        ok = await delete_presentation(presentation_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Presentation not found or not owned by you")
        return {"status": "deleted"}

    @app.get("/api/v1/presentations/{presentation_id}/file")
    async def presentation_file(presentation_id: str, request: Request) -> Response:
        user_id = _require_user_id(request)
        presentation = await get_presentation(presentation_id)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if presentation["status"] != "published" and presentation["owner_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="This draft belongs to another user")

        fields = (
            presentation["published_snapshot"]
            if presentation["status"] == "published"
            else presentation["fields"]
        )
        data = collected_to_incident_dict(fields or {})
        html = build_full_html(
            data,
            datetime.now().strftime("%d.%m.%Y"),
            analysis_markdown=presentation.get("analysis_markdown") or "",
        )
        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{presentation_id}.html"'},
        )

    @app.get("/api/v1/threads")
    async def list_threads(request: Request) -> dict[str, Any]:
        user_id = _require_user_id(request)
        return {"threads": await get_threads_by_user(user_id)}

    @app.post("/api/v1/threads", status_code=201)
    async def create_thread(request: Request) -> dict[str, str]:
        user_id = _require_user_id(request)
        thread_id = str(uuid.uuid4())
        await create_thread_db(thread_id, user_id)
        return {"thread_id": thread_id}

    @app.delete("/api/v1/threads/{thread_id}")
    async def delete_thread(thread_id: str, request: Request) -> dict[str, str]:
        user_id = _require_user_id(request)
        deleted = await delete_thread_db(thread_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Thread not found")
        return {"status": "deleted"}

    @app.get("/api/v1/threads/{thread_id}/messages")
    async def get_thread_messages(thread_id: str, request: Request) -> dict[str, Any]:
        _require_user_id(request)
        return {"messages": await get_messages_db(thread_id)}

    @app.post("/api/v1/threads/{thread_id}/messages")
    async def post_thread_message(
        thread_id: str,
        payload: ChatMessageRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_user_id(request)
        await add_message_db(thread_id, "user", payload.message)

        graph = request.app.state.graph
        config = _config(thread_id)
        lock: asyncio.Lock = request.app.state.thread_locks[thread_id]
        async with lock:
            snapshot = await graph.aget_state(config)
            awaiting = bool(snapshot.interrupts)
            graph_input = await _build_graph_input(
                graph,
                config,
                thread_id,
                text=payload.message,
                structured_payload=None,
                awaiting=awaiting,
            )
            try:
                output = await graph.ainvoke(graph_input, config=config)
            except Exception as exc:
                logger.error("post_thread_message error: %s", exc, exc_info=True)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        new_snapshot = await graph.aget_state(config)
        if bool(new_snapshot.interrupts):
            interrupt_payload = _interrupt_payload_from_snapshot(new_snapshot) or {}
            question = interrupt_payload.get("question", "Уточните запрос")
            await add_message_db(thread_id, "assistant", question)
            return {
                "response": question,
                "artifact": None,
                "awaiting_input": True,
                "tool_calls": [interrupt_to_tool_call(interrupt_payload).model_dump()],
            }

        messages = output.get("messages", []) if isinstance(output, dict) else []
        last_ai = messages[-1] if messages else None
        content = getattr(last_ai, "content", None) if last_ai else "Готово"
        artifact_id = output.get("current_artifact_id") if isinstance(output, dict) else None
        artifact = output.get("artifacts", {}).get(artifact_id) if artifact_id and isinstance(output, dict) else None
        await add_message_db(thread_id, "assistant", content, artifact)
        return {"response": content, "artifact": artifact, "awaiting_input": False, "tool_calls": None}

    @app.get("/threads/{thread_id}", response_model=ThreadStateResponse)
    async def thread_state(thread_id: str, request: Request) -> ThreadStateResponse:
        graph = request.app.state.graph
        snapshot = await graph.aget_state(_config(thread_id))
        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        interrupt_payload = _interrupt_payload_from_snapshot(snapshot)
        awaiting = bool(snapshot.interrupts)
        artifact_id = values.get("current_artifact_id")
        pending_artifact = values.get("artifacts", {}).get(artifact_id) if awaiting and artifact_id else None

        return ThreadStateResponse(
            thread_id=thread_id,
            awaiting_input=awaiting,
            question=(interrupt_payload or {}).get("question"),
            pending_artifact=pending_artifact,
            next_nodes=list(snapshot.next or ()),
            current_artifact_id=artifact_id,
            tool_calls=[interrupt_to_tool_call(interrupt_payload)] if awaiting and interrupt_payload else None,
        )

    @app.post("/message")
    async def message(payload: MessageRequest, request: Request) -> EventSourceResponse:
        """Unified SSE endpoint for ordinary messages and interrupt answers.

        A preflight check produces a normal HTTP 409/422 whenever possible.
        The same validation is repeated under the per-thread lock, before
        Command(resume=...), so a parallel request cannot resume a newer
        interrupt using a stale tool_call_id.
        """
        if payload.text is None and payload.payload is None:
            raise HTTPException(status_code=422, detail="Нужно передать text или payload")

        graph = request.app.state.graph
        config = _config(payload.thread_id)
        lock: asyncio.Lock = request.app.state.thread_locks[payload.thread_id]

        preflight_snapshot = await graph.aget_state(config)
        _validate_interrupt_response(preflight_snapshot, payload)

        async def event_source():
            persisted_content: str | None = None
            persisted_artifact: dict[str, Any] | None = None

            async with lock:
                snapshot = await graph.aget_state(config)
                try:
                    awaiting = _validate_interrupt_response(snapshot, payload)
                except StaleInterruptError as exc:
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"message": exc.detail, "status_code": exc.status_code},
                            ensure_ascii=False,
                        ),
                    }
                    return

                graph_input = await _build_graph_input(
                    graph,
                    config,
                    payload.thread_id,
                    text=payload.text,
                    structured_payload=payload.payload,
                    awaiting=awaiting,
                )
                user_content = payload.display_text or payload.text or json.dumps(payload.payload, ensure_ascii=False)
                await add_message_db(payload.thread_id, "user", user_content)

                async for event in stream_graph_events(graph, graph_input, config, suppress=awaiting):
                    if event["event"] == "message":
                        data = json.loads(event["data"])
                        persisted_content = data.get("content")
                        persisted_artifact = data.get("artifact")
                    yield event

            if persisted_content:
                await add_message_db(payload.thread_id, "assistant", persisted_content, persisted_artifact)

        return EventSourceResponse(event_source())

    return app


def _validate_interrupt_response(snapshot: Any, payload: MessageRequest) -> bool:
    awaiting = bool(snapshot.interrupts)

    if not awaiting and payload.text is None:
        raise HTTPException(status_code=409, detail="Тред не ждёт структурированного ответа, передайте text")

    if awaiting:
        if not payload.tool_call_id:
            raise HTTPException(status_code=422, detail="Для ответа на уточняющий вопрос требуется tool_call_id")

        current_payload = _interrupt_payload_from_snapshot(snapshot) or {}
        expected_id = interrupt_to_tool_call(current_payload).id
        if payload.tool_call_id != expected_id:
            raise StaleInterruptError(
                status_code=409,
                detail="Уточняющий вопрос уже изменился. Обновите страницу и ответьте на актуальный вопрос.",
            )

    return awaiting


def _require_user_id(request: Request) -> str:
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-ID")
    return user_id


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}


async def _build_graph_input(
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    *,
    text: str | None,
    structured_payload: dict[str, Any] | None,
    awaiting: bool,
) -> Any:
    if awaiting:
        return Command(resume=structured_payload or text)

    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values or not snapshot.values.get("thread_id")
    if is_new_thread:
        return build_initial_state(thread_id, HumanMessage(content=text))
    return {"messages": [HumanMessage(content=text)]}


def _interrupt_payload_from_snapshot(snapshot: Any) -> dict[str, Any] | None:
    for item in snapshot.interrupts or ():
        value = getattr(item, "value", None)
        if isinstance(value, dict):
            return value
    return None


app = create_app()
